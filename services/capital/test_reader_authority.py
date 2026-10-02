"""
Tests for Capital reader authority and runtime-manager read-only access.
Validates:
1. Runtime-manager read of a pool and of bindings admissibility succeeds for its tenant.
2. Runtime-manager mutation is rejected (ACTOR_SERVICE_FORBIDDEN 403).
3. Unlisted service read is rejected (ACTOR_SERVICE_FORBIDDEN 403).
4. Cross-tenant read is rejected (404/403).
5. Isolated run of deploy authority capital pool and admissibility proofs against Capital app
   no longer returns HTTP 400.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import time
import urllib.error
from pathlib import Path
from typing import Any, Mapping
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from services.runtime_auth_inbound import encode_jwt_hs256


JWT_SECRET = "test-capital-reader-secret-32bytes!!"


@pytest.fixture()
def capital_reader_env():
    tempdir = tempfile.mkdtemp(prefix="capital_reader_test_")
    keys = (
        "CAPITAL_DATA_DIR",
        "PANTHEON_GOVERNANCE_DATA_DIR",
        "CAPITAL_STORE_BACKEND",
        "CAPITAL_AUDIT_BACKEND",
        "CAPITAL_AUTH_DISABLED",
        "CAPITAL_AUTH_MODE",
        "CAPITAL_JWT_SECRET",
        "CAPITAL_ALLOWED_CALLER_SERVICES",
        "CAPITAL_ALLOWED_READER_SERVICES",
    )
    backup = {k: os.environ.get(k) for k in keys}
    os.environ.update(
        {
            "CAPITAL_DATA_DIR": tempdir,
            "PANTHEON_GOVERNANCE_DATA_DIR": tempdir,
            "CAPITAL_STORE_BACKEND": "json",
            "CAPITAL_AUDIT_BACKEND": "jsonl",
            "CAPITAL_AUTH_DISABLED": "false",
            "CAPITAL_AUTH_MODE": "strict",
            "CAPITAL_JWT_SECRET": JWT_SECRET,
            "CAPITAL_ALLOWED_CALLER_SERVICES": "control-plane-bff",
            "CAPITAL_ALLOWED_READER_SERVICES": "control-plane-bff,runtime-manager",
        }
    )
    sys.modules.pop("services.capital.main", None)
    module = importlib.import_module("services.capital.main")
    module = importlib.reload(module)

    client = TestClient(module.app)
    try:
        yield client, module, Path(tempdir)
    finally:
        for k, v in backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        sys.modules.pop("services.capital.main", None)


def _token(
    *,
    service: str,
    tenant_id: str,
    roles: list[str],
    actor_id: str = "actor-1",
) -> str:
    return encode_jwt_hs256(
        {
            "sub": service,
            "service": service,
            "roles": roles,
            "allowed_tenants": [tenant_id],
            "tenant_id": tenant_id,
            "delegated_actor_id": actor_id,
            "exp": int(time.time()) + 3600,
        },
        secret=JWT_SECRET,
    )


def _auth_headers(
    tenant_id: str,
    *,
    service: str = "control-plane-bff",
    roles: list[str] | None = None,
    actor_id: str = "actor-1",
) -> dict[str, str]:
    t = _token(
        service=service,
        tenant_id=tenant_id,
        roles=roles or ["capital.admin", "persona.admin", "operator", "approver", "admin"],
        actor_id=actor_id,
    )
    return {
        "Authorization": f"Bearer {t}",
        "X-Pantheon-Service": service,
        "X-Tenant-Id": tenant_id,
    }


def _seed_pool_and_binding(client: TestClient, tenant_id: str = "tenant-alpha"):
    headers = _auth_headers(tenant_id, service="control-plane-bff", actor_id="admin-1")
    # 1. Create pool
    pool_res = client.post(
        "/api/capital-pools",
        json={
            "actor_id": "admin-1",
            "actor_role": "capital.admin",
            "pool_id": "pool-alpha-1",
            "name": "Alpha Pool",
            "owner_id": "fund-alpha",
            "owner_type": "fund",
            "approval_decision_id": "approval-pool-create",
            "risk_policy_ref": "risk-main",
        },
        headers=headers,
    )
    assert pool_res.status_code == 201, pool_res.text

    # 2. Create binding
    bind_res = client.post(
        "/api/bindings",
        json={
            "actor_id": "admin-1",
            "actor_role": "persona.admin",
            "binding_id": "binding-alpha-1",
            "persona_id": "persona-alpha",
            "capital_pool_id": "pool-alpha-1",
            "capital_sleeve_id": "sleeve-1",
            "role": "live_owner",
            "allowed_deployment_scope": "paper",
        },
        headers=headers,
    )
    assert bind_res.status_code == 201, bind_res.text

    # 3. Activate binding
    act_res = client.post(
        "/api/bindings/binding-alpha-1/activate",
        json={
            "actor_id": "admin-1",
            "actor_role": "persona.admin",
            "approval_decision_id": "app-alpha-1",
        },
        headers=headers,
    )
    assert act_res.status_code == 200, act_res.text


def test_runtime_manager_read_pool_and_admissibility_succeeds(capital_reader_env):
    client, module, _ = capital_reader_env
    _seed_pool_and_binding(client, "tenant-alpha")

    rm_headers = _auth_headers(
        "tenant-alpha",
        service="runtime-manager",
        roles=["capital-reader"],
        actor_id="rm-actor",
    )

    # 1. Read capital pool
    res_pool = client.get("/api/capital-pools/pool-alpha-1", headers=rm_headers)
    assert res_pool.status_code == 200, res_pool.text
    pool_data = res_pool.json()
    assert pool_data["pool_id"] == "pool-alpha-1"
    assert pool_data["tenant_id"] == "tenant-alpha"
    assert pool_data["status"] == "active"

    # 2. Read bindings admissibility
    res_admissibility = client.get(
        "/api/bindings/admissibility",
        params={
            "persona_id": "persona-alpha",
            "capital_pool_id": "pool-alpha-1",
            "target_stage": "paper",
        },
        headers=rm_headers,
    )
    assert res_admissibility.status_code == 200, res_admissibility.text
    admissibility_data = res_admissibility.json()
    assert admissibility_data["permitted"] is True
    assert admissibility_data["binding_id"] == "binding-alpha-1"
    assert admissibility_data["capital_pool_id"] == "pool-alpha-1"

    # 3. Read binding by ID
    res_binding = client.get("/api/bindings/binding-alpha-1", headers=rm_headers)
    assert res_binding.status_code == 200, res_binding.text
    assert res_binding.json()["binding_id"] == "binding-alpha-1"


def test_runtime_manager_mutation_is_rejected(capital_reader_env):
    client, module, _ = capital_reader_env
    _seed_pool_and_binding(client, "tenant-alpha")

    rm_headers = _auth_headers(
        "tenant-alpha",
        service="runtime-manager",
        roles=["capital-reader", "capital.admin", "persona.admin"],
        actor_id="rm-actor",
    )

    # POST /api/capital-pools fails: caller service runtime-manager is not authorized for mutations
    res_create_pool = client.post(
        "/api/capital-pools",
        json={
            "actor_id": "rm-actor",
            "actor_role": "capital.admin",
            "pool_id": "pool-forbidden",
            "name": "Forbidden Pool",
            "owner_id": "fund",
            "owner_type": "fund",
            "approval_decision_id": "app",
        },
        headers=rm_headers,
    )
    assert res_create_pool.status_code == 403
    assert res_create_pool.json()["error"]["code"] == "ACTOR_SERVICE_FORBIDDEN"

    # POST /api/bindings fails
    res_create_bind = client.post(
        "/api/bindings",
        json={
            "actor_id": "rm-actor",
            "actor_role": "persona.admin",
            "binding_id": "bind-forbidden",
            "persona_id": "persona-forbidden",
            "capital_pool_id": "pool-alpha-1",
            "role": "live_owner",
            "allowed_deployment_scope": "paper",
        },
        headers=rm_headers,
    )
    assert res_create_bind.status_code == 403
    assert res_create_bind.json()["error"]["code"] == "ACTOR_SERVICE_FORBIDDEN"

    # PATCH status fails
    res_patch = client.patch(
        "/api/capital-pools/pool-alpha-1/status",
        json={"actor_id": "rm-actor", "actor_role": "capital.admin", "status": "suspended"},
        headers=rm_headers,
    )
    assert res_patch.status_code == 403
    assert res_patch.json()["error"]["code"] == "ACTOR_SERVICE_FORBIDDEN"


def test_unlisted_service_read_is_rejected(capital_reader_env):
    client, module, _ = capital_reader_env
    _seed_pool_and_binding(client, "tenant-alpha")

    unlisted_headers = _auth_headers(
        "tenant-alpha",
        service="unlisted-external-service",
        roles=["capital-reader"],
        actor_id="ext-actor",
    )

    res_get_pool = client.get("/api/capital-pools/pool-alpha-1", headers=unlisted_headers)
    assert res_get_pool.status_code == 403
    assert res_get_pool.json()["error"]["code"] == "ACTOR_SERVICE_FORBIDDEN"

    res_get_admissibility = client.get(
        "/api/bindings/admissibility",
        params={
            "persona_id": "persona-alpha",
            "capital_pool_id": "pool-alpha-1",
            "target_stage": "paper",
        },
        headers=unlisted_headers,
    )
    assert res_get_admissibility.status_code == 403
    assert res_get_admissibility.json()["error"]["code"] == "ACTOR_SERVICE_FORBIDDEN"


def test_cross_tenant_read_is_rejected(capital_reader_env):
    client, module, _ = capital_reader_env
    _seed_pool_and_binding(client, "tenant-alpha")

    rm_headers_beta = _auth_headers(
        "tenant-beta",
        service="runtime-manager",
        roles=["capital-reader"],
        actor_id="rm-actor",
    )

    # 1. Tenant beta cannot read tenant alpha's pool (returns 404)
    res_cross_pool = client.get("/api/capital-pools/pool-alpha-1", headers=rm_headers_beta)
    assert res_cross_pool.status_code == 404

    # 2. Tenant beta cannot read tenant alpha's bindings admissibility (returns 404)
    res_cross_admissibility = client.get(
        "/api/bindings/admissibility",
        params={
            "persona_id": "persona-alpha",
            "capital_pool_id": "pool-alpha-1",
            "target_stage": "paper",
        },
        headers=rm_headers_beta,
    )
    assert res_cross_admissibility.status_code == 404

    # 3. Requesting tenant-alpha explicitly when token is bound to tenant-beta fails with 403
    forged_headers = dict(rm_headers_beta, **{"X-Tenant-Id": "tenant-alpha"})
    res_forged = client.get("/api/capital-pools/pool-alpha-1", headers=forged_headers)
    assert res_forged.status_code == 403
    assert res_forged.json()["error"]["code"] == "TENANT_SCOPE_FORBIDDEN"


def test_isolated_run_deploy_authority_against_capital_app(capital_reader_env, monkeypatch):
    """
    Proves that a local isolated run of the deploy authority capital pool and
    admissibility proofs against the Capital app succeeds and no longer returns HTTP 400.
    """
    client, module, _ = capital_reader_env
    _seed_pool_and_binding(client, "tenant-alpha")

    # Import deploy authority
    _rm_dir = str(Path(__file__).resolve().parents[1] / "runtime-manager")
    if _rm_dir not in sys.path:
        sys.path.insert(0, _rm_dir)
    import deploy_authority as deploy_auth

    rm_token = _token(
        service="runtime-manager",
        tenant_id="tenant-alpha",
        roles=["capital-reader"],
        actor_id="rm-deploy-verifier",
    )
    monkeypatch.setenv("RUNTIME_MANAGER_CAPITAL_SERVICE_TOKEN", rm_token)
    monkeypatch.setenv("PANTHEON_DEPLOYMENT_TENANT_ID", "tenant-alpha")

    # Custom fetcher simulating deploy authority HTTP client calling Capital app TestClient
    def client_fetch(url: str, timeout: float, headers: Mapping[str, str] | None = None) -> Mapping[str, Any]:
        path_and_query = url.split("http://capital:8092", 1)[-1]
        response = client.get(path_and_query, headers=dict(headers or {}))
        if response.status_code >= 400:
            raise deploy_auth.DeployAuthorityError(f"HTTP {response.status_code}: {response.text}")
        return response.json()

    # Monkeypatch _fetch_json in deploy_authority
    monkeypatch.setattr(deploy_auth, "_fetch_json", client_fetch)

    # 1. Prove that without capital headers (raw unauthenticated fetch), Capital returns HTTP 400
    with pytest.raises(deploy_auth.DeployAuthorityError) as unauth_err:
        client_fetch("http://capital:8092/api/capital-pools/pool-alpha-1", 5.0, headers={})
    assert "HTTP 400" in str(unauth_err.value)

    # 2. Prove that with _capital_request_headers(), capital proofs against Capital app SUCCEED (no 400)
    capital_headers = deploy_auth._capital_request_headers("tenant-alpha")
    pool_proof = client_fetch("http://capital:8092/api/capital-pools/pool-alpha-1", 5.0, headers=capital_headers)
    assert pool_proof["pool_id"] == "pool-alpha-1"
    assert pool_proof["status"] == "active"

    admissibility_proof = client_fetch(
        "http://capital:8092/api/bindings/admissibility?persona_id=persona-alpha&capital_pool_id=pool-alpha-1&target_stage=paper",
        5.0,
        headers=capital_headers,
    )
    assert admissibility_proof["permitted"] is True
    assert admissibility_proof["binding_id"] == "binding-alpha-1"

    binding_proof = client_fetch(
        "http://capital:8092/api/bindings/binding-alpha-1",
        5.0,
        headers=capital_headers,
    )
    assert binding_proof["binding_id"] == "binding-alpha-1"
    assert binding_proof["status"] == "active"

    # 3. Regression: Invoke full verify_deploy_authorities through the real caller transport
    # (services/deployment/outbox_consumer_worker._fetch_authority_json) against the Capital app.
    from services.deployment import outbox_consumer_worker as worker

    monkeypatch.setenv("PANTHEON_CAPITAL_API_URL", "http://capital:8092")
    monkeypatch.setenv("DEPLOYMENT_API_URL", "http://deployment:8095")
    monkeypatch.setenv("PANTHEON_DEPLOYMENT_SERVICE_TOKEN", "deployment-test-token")

    from services.registry.strategy_artifact import (
        BUILTIN_STRATEGY_ARTIFACT_PATHS,
        load_strategy_artifact_registration,
        strategy_artifact_checksum,
    )
    from services.governance.test_approval_authority import (
        SnapshotApprovalReader,
        approval_snapshot,
    )

    registration = load_strategy_artifact_registration(
        BUILTIN_STRATEGY_ARTIFACT_PATHS[0]
    )
    artifact = registration["strategy_artifact"]
    artifact_id = artifact["artifact_id"]

    def real_caller_open(self, req, *args, **kwargs):
        full_url = req.full_url
        if "http://capital:8092" in full_url:
            path_and_query = full_url.split("http://capital:8092", 1)[-1]
            response = client.get(path_and_query, headers=dict(req.headers))
            if response.status_code >= 400:
                raise urllib.error.HTTPError(
                    full_url, response.status_code, response.text, dict(response.headers), None
                )
            resp = MagicMock()
            resp.read.return_value = response.content
            resp.__enter__.return_value = resp
            return resp
        elif "http://deployment:8095" in full_url:
            plan_payload = {
                "plan_id": "plan-alpha-1",
                "status": "approved",
                "current_stage": "none",
                "target_stage": "paper",
                "artifact_id": artifact_id,
                "artifact_version": artifact["version"],
                "strategy_id": artifact["strategy_id"],
                "approval_decision_id": "appr-alpha-1",
                "capital_pool_id": "pool-alpha-1",
                "sponsor_persona_id": "persona-alpha",
                "persona_capital_binding_id": "binding-alpha-1",
                "persona_capital_binding_status": "active",
                "allowed_deployment_scope": "paper",
                "checksum": "checksum-alpha",
                "transition_type": "activate",
                "runtime_action": "start",
                "runtime_lifecycle": {"state": "active"},
                "scale": {"capital_scale_pct": 0.0},
                "rollback": {},
                "metadata": {"tenant_id": "tenant-alpha"},
            }
            resp = MagicMock()
            resp.read.return_value = json.dumps(plan_payload).encode("utf-8")
            resp.__enter__.return_value = resp
            return resp
        raise AssertionError(f"unexpected url: {full_url}")

    with patch("urllib.request.OpenerDirector.open", real_caller_open):
        # A) Prove that without capital token, the real caller transport returns HTTP 401/400
        with monkeypatch.context() as m:
            m.delenv("RUNTIME_MANAGER_CAPITAL_SERVICE_TOKEN", raising=False)
            m.delenv("DEPLOYMENT_CAPITAL_SERVICE_TOKEN", raising=False)
            with pytest.raises(deploy_auth.DeployAuthorityError) as caller_unauth_err:
                worker._fetch_authority_json("http://capital:8092/api/capital-pools/pool-alpha-1", 5.0)
            assert any(code in str(caller_unauth_err.value) for code in ("HTTP 400", "HTTP 401"))

        # B) Prove that with the scoped token, full verify_deploy_authorities passes
        registry_payload = {
            "entry": {
                "registry_id": artifact_id,
                "artifact_type": "execution_bundle",
                "strategy_id": artifact["strategy_id"],
                "version": artifact["version"],
                "artifact_state": "approved",
                "checksum": strategy_artifact_checksum(artifact),
                "approval_decision_id": "appr-alpha-1",
                "metadata": {"strategy_artifact": artifact},
            },
            "deployment_stage": "none",
        }

        approval_evidence = approval_snapshot(
            candidate_digest=registry_payload["entry"]["checksum"],
            decision_id="appr-alpha-1",
            decision_state="decided",
            decision="approved",
            target_type="registry_entry",
            target_id=artifact_id,
            target_version=artifact["version"],
            capital_pool_id="pool-alpha-1",
            persona_id="persona-alpha",
            tenant_id="tenant-alpha",
            actor_id="governance-reviewer",
            conditions=[],
            expires_at="2099-01-01T00:00:00Z",
            revoked_at=None,
        )
        approval_reader = SnapshotApprovalReader(approval_evidence)

        authority_report = deploy_auth.verify_deploy_authorities(
            request={
                "plan_id": "plan-alpha-1",
                "plan_status": "approved",
                "target_stage": "paper",
                "artifact_id": artifact_id,
                "artifact_version": artifact["version"],
                "strategy_id": artifact["strategy_id"],
                "approval_decision_id": "appr-alpha-1",
                "capital_pool_id": "pool-alpha-1",
                "sponsor_persona_id": "persona-alpha",
                "persona_capital_binding_id": "binding-alpha-1",
                "persona_capital_binding_status": "active",
                "allowed_deployment_scope": "paper",
            },
            deployment_base_url="http://deployment:8095",
            registry_base_url="http://registry:8087",
            governance_base_url="http://governance:8082",
            capital_base_url="http://capital:8092",
            approval_reader=approval_reader,
            registry_fetch_json=lambda url, timeout: registry_payload,
            fetch_json=worker._fetch_authority_json,
        )
        assert authority_report["status"] == "passed"
        assert authority_report["capital_pool_id"] == "pool-alpha-1"
        assert authority_report["sponsor_persona_id"] == "persona-alpha"
        assert authority_report["persona_capital_binding_id"] == "binding-alpha-1"
        assert authority_report["capital_pool_sha256"].startswith("sha256:")
        assert authority_report["capital_admissibility_sha256"].startswith("sha256:")
        assert authority_report["persona_capital_binding_sha256"].startswith("sha256:")

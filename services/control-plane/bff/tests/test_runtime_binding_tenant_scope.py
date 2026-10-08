"""RUNTIME-BINDING-TENANT-SCOPE-20261007.

Drives the real runtime-manager Flask app (deploy + list) through the real
deploy-authority verifier, then applies the Management AI context tenant
filter to what the app returns.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from services.runtime_auth_inbound import encode_jwt_hs256

from services.control_plane.bff.assistant.management_service import (
    _mgmt_nl_filter_tenant_records,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
RUNTIME_DIR = REPO_ROOT / "services" / "runtime-manager"
TENANT = "tenant-unit"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def runtime_app(tmp_path, monkeypatch):
    monkeypatch.setenv("PANTHEON_RUNTIME_BINDING_STORE_PATH", str(tmp_path / "b.json"))
    monkeypatch.setenv("PANTHEON_COMMAND_STATE_FILE", str(tmp_path / "c.json"))
    monkeypatch.setenv("PANTHEON_SINGLE_RUNTIME_ENFORCED", "true")
    monkeypatch.setenv("PANTHEON_DEPLOYMENT_API_URL", "http://deployment")
    monkeypatch.syspath_prepend(str(RUNTIME_DIR))
    monkeypatch.syspath_prepend(str(REPO_ROOT))
    monkeypatch.delitem(sys.modules, "main", raising=False)
    main = _load("main", RUNTIME_DIR / "main.py")
    monkeypatch.setitem(sys.modules, "main", main)
    main._svc = None
    facts = _load("tenant_scope_authority_facts", RUNTIME_DIR / "test_deploy_authority.py")
    yield main, facts
    sys.modules.pop("main", None)


def _deploy(main, facts, plan_tenant, monkeypatch):
    request, registry, approval, plan, pool, persona_binding = facts._facts()
    plan["metadata"] = {"tenant_id": TENANT}
    real_verify = facts.authority.verify_deploy_authorities

    def verify(body, **kw):
        report = real_verify_with_fixtures(body)
        # The real verifier refuses a tenantless plan, so model one by
        # dropping the authoritative tenant from an otherwise real report.
        return {**report, "tenant_id": plan_tenant}

    real_verify_with_fixtures = lambda body: real_verify(
        body,
        deployment_base_url="http://deployment",
        registry_base_url="http://registry",
        governance_base_url="http://governance",
        capital_base_url="http://capital",
        approval_reader=facts.SnapshotApprovalReader(approval),
        registry_fetch_json=lambda url, timeout: registry,
        capital_fetch_json=facts._fetcher(registry, approval, plan, pool, persona_binding),
        fetch_json=facts._fetcher(registry, approval, plan, pool, persona_binding),
        now=datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(main, "verify_deploy_authorities", verify)
    request["loader_checks_passed"] = True
    client = main.app.test_client()
    response = client.post(
        "/api/runtimes/deploy",
        headers={"Authorization": "Bearer alice:operator", "X-MFA-Token": "123456"},
        json=request,
    )
    assert response.status_code == 201, response.get_data(as_text=True)
    listed = client.get(
        "/api/runtime-bindings",
        headers={"Authorization": "Bearer alice:operator"},
    )
    assert listed.status_code == 200, listed.get_data(as_text=True)
    payload = listed.get_json()
    return payload if isinstance(payload, list) else payload.get("items") or payload.get("bindings")


def test_tenant_scoped_context_sees_binding_created_for_that_tenant(runtime_app, monkeypatch):
    main, facts = runtime_app
    rows = _deploy(main, facts, TENANT, monkeypatch)
    assert len(rows) == 1
    assert _mgmt_nl_filter_tenant_records(rows, TENANT) == rows
    assert _mgmt_nl_filter_tenant_records(rows, "tenant-other") == []


def test_binding_without_authoritative_tenant_stays_excluded(runtime_app, monkeypatch):
    main, facts = runtime_app
    rows = _deploy(main, facts, None, monkeypatch)
    assert len(rows) == 1
    assert _mgmt_nl_filter_tenant_records(rows, TENANT) == []
    assert _mgmt_nl_filter_tenant_records(rows, None) == []


OPERATOR = {"Authorization": "Bearer alice:operator", "X-MFA-Token": "123456"}


def _tenant_binding(main, facts, monkeypatch):
    rows = _deploy(main, facts, TENANT, monkeypatch)
    return main._get_service().require(rows[0]["binding_id"])


def _assert_child_scoped_to_parent_tenant(child):
    assert child["metadata"]["tenant_id"] == TENANT
    assert _mgmt_nl_filter_tenant_records([child], TENANT) == [child]
    assert _mgmt_nl_filter_tenant_records([child], "tenant-other") == []


def test_rollback_binding_inherits_parent_tenant_not_caller_tenant(runtime_app, monkeypatch):
    main, facts = runtime_app
    parent = _tenant_binding(main, facts, monkeypatch)
    helpers = _load("tenant_scope_runtime_helpers", RUNTIME_DIR / "test_runtime_manager.py")
    prior = helpers._seed_retired_rollback_target(
        main._get_service(),
        old_binding=parent,
        plan_id="plan-prior",
        artifact_id="artifact-prior",
        artifact_version="0.9.0",
    )
    response = main.app.test_client().post(
        "/api/rollback",
        headers=OPERATOR,
        json={
            "current_binding_id": parent.binding_id,
            "action_type": "replace",
            "replacement_plan_id": "plan-prior",
            "replacement_plan_status": "approved",
            "replacement_artifact_id": "artifact-prior",
            "replacement_artifact_version": "0.9.0",
            "replacement_persona_capital_binding_id": parent.persona_capital_binding_id,
            "replacement_allowed_deployment_scope": "paper",
            "replacement_runtime_id": "rt-rollback-tenant",
            "replacement_metadata": {"tenant_id": "tenant-other"},
            "human_gate_decision": helpers._approved_rollback_human_gate(
                old_binding_id=parent.binding_id,
                prior_binding_id=prior.binding_id,
                target_environment=parent.deployment_mode,
            ),
        },
    )
    assert response.status_code == 201, response.get_data(as_text=True)
    _assert_child_scoped_to_parent_tenant(response.get_json()["new_binding"])


def test_promotion_binding_inherits_parent_tenant_not_caller_tenant(runtime_app, monkeypatch):
    main, facts = runtime_app
    parent = _tenant_binding(main, facts, monkeypatch)
    helpers = _load("tenant_scope_promotion_helpers", RUNTIME_DIR / "test_stage_promotion_http.py")
    body = {
        **helpers._promotion_request(parent.binding_id),
        "artifact_id": parent.artifact_id,
        "artifact_version": parent.artifact_version,
        "capital_pool_id": parent.capital_pool_id,
        "persona_capital_binding_id": parent.persona_capital_binding_id,
        "runtime_id": parent.runtime_id,
    }
    body["metadata"]["tenant_id"] = "tenant-other"
    monkeypatch.setenv("PANTHEON_CANARY_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("PANTHEON_RUNTIME_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_RUNTIME_JWT_SECRET", "tenant-scope-secret")
    token = encode_jwt_hs256(
        {"sub": "operator-b", "roles": ["operator"], "amr": ["mfa"]},
        secret="tenant-scope-secret",
    )
    monkeypatch.setattr(main, "_canonicalize_promotion_body", lambda b, **_: b)
    response = main.app.test_client().post(
        f"/api/runtime-bindings/{parent.binding_id}/promote",
        headers={"Authorization": f"Bearer {token}"},
        json=body,
    )
    assert response.status_code == 201, response.get_data(as_text=True)
    _assert_child_scoped_to_parent_tenant(response.get_json()["new_binding"])

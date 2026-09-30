"""
Comprehensive tests for multi-tenant isolation, row scoping, and migration backfill
across all 23 Capital service routes.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

from services.runtime_auth_inbound import encode_jwt_hs256
from services.capital.allocation_store import allocation_line_digest


JWT_SECRET = "test-capital-secret-key-32bytes!!"
TESTED_ROUTES: set[str] = set()


@pytest.fixture()
def capital_test_env():
    tempdir = tempfile.mkdtemp(prefix="capital_tenant_test_")
    keys = (
        "CAPITAL_DATA_DIR",
        "PANTHEON_GOVERNANCE_DATA_DIR",
        "CAPITAL_STORE_BACKEND",
        "CAPITAL_AUDIT_BACKEND",
        "CAPITAL_AUTH_DISABLED",
        "CAPITAL_AUTH_MODE",
        "CAPITAL_JWT_SECRET",
        "CAPITAL_ALLOWED_CALLER_SERVICES",
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


def _auth_headers(
    tenant_id: str,
    roles: list[str] | None = None,
    service: str = "control-plane-bff",
    actor_id: str = "actor-1",
    omit_tenant_header: bool = False,
) -> dict[str, str]:
    token = encode_jwt_hs256(
        {
            "sub": service,
            "service": service,
            "roles": roles
            or [
                "capital.admin",
                "persona.admin",
                "operator",
                "approver",
                "admin",
                "viewer",
                "reader",
                "capital-reader",
            ],
            "allowed_tenants": [tenant_id],
            "delegated_actor_id": actor_id,
            "exp": int(time.time()) + 3600,
        },
        secret=JWT_SECRET,
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Pantheon-Service": service,
    }
    if not omit_tenant_header:
        headers["X-Tenant-Id"] = tenant_id
    return headers


def test_23_routes_same_tenant_and_cross_tenant_isolation(capital_test_env):
    client, module, tempdir = capital_test_env
    headers_a = _auth_headers("tenant-alpha", actor_id="admin-alpha")
    headers_b = _auth_headers("tenant-beta", actor_id="admin-beta")

    # 1. POST /api/capital-pools
    route_1 = "POST /api/capital-pools"
    TESTED_ROUTES.add(route_1)
    res = client.post(
        "/api/capital-pools",
        json={
            "actor_id": "admin-alpha",
            "actor_role": "capital.admin",
            "pool_id": "pool-alpha-1",
            "name": "Alpha Pool",
            "owner_id": "fund-alpha",
            "owner_type": "fund",
            "risk_policy_ref": "risk-main",
        },
        headers=headers_a,
    )
    assert res.status_code == 201, res.text
    assert res.headers["X-Pantheon-Tenant"] == "tenant-alpha"
    assert res.json()["tenant_id"] == "tenant-alpha"

    # Also test token without X-Tenant-Id header extracts tenant from claims
    headers_a_no_header = _auth_headers("tenant-alpha", actor_id="admin-alpha", omit_tenant_header=True)
    res_b_pool = client.post(
        "/api/capital-pools",
        json={
            "actor_id": "admin-beta",
            "actor_role": "capital.admin",
            "pool_id": "pool-beta-1",
            "name": "Beta Pool",
            "owner_id": "fund-beta",
            "owner_type": "fund",
            "risk_policy_ref": "risk-main",
        },
        headers=headers_b,
    )
    assert res_b_pool.status_code == 201
    assert res_b_pool.headers["X-Pantheon-Tenant"] == "tenant-beta"

    # 2. GET /api/capital-pools
    route_2 = "GET /api/capital-pools"
    TESTED_ROUTES.add(route_2)
    res = client.get("/api/capital-pools", headers=headers_a)
    assert res.status_code == 200
    assert res.headers["X-Pantheon-Tenant"] == "tenant-alpha"
    pool_ids_a = [p["pool_id"] for p in res.json()]
    assert "pool-alpha-1" in pool_ids_a
    assert "pool-beta-1" not in pool_ids_a

    res_b = client.get("/api/capital-pools", headers=headers_b)
    pool_ids_b = [p["pool_id"] for p in res_b.json()]
    assert "pool-beta-1" in pool_ids_b
    assert "pool-alpha-1" not in pool_ids_b

    # 3. GET /api/capital-pools/{pool_id}
    route_3 = "GET /api/capital-pools/{pool_id}"
    TESTED_ROUTES.add(route_3)
    res = client.get("/api/capital-pools/pool-alpha-1", headers=headers_a_no_header)
    assert res.status_code == 200
    assert res.json()["pool_id"] == "pool-alpha-1"
    assert res.json()["tenant_id"] == "tenant-alpha"
    # Cross tenant
    res_cross = client.get("/api/capital-pools/pool-alpha-1", headers=headers_b)
    assert res_cross.status_code == 404

    # 4. PATCH /api/capital-pools/{pool_id}/status
    route_4 = "PATCH /api/capital-pools/{pool_id}/status"
    TESTED_ROUTES.add(route_4)
    res_cross = client.patch(
        "/api/capital-pools/pool-alpha-1/status",
        json={"actor_id": "admin-beta", "actor_role": "capital.admin", "status": "suspended"},
        headers=headers_b,
    )
    assert res_cross.status_code == 404

    res = client.patch(
        "/api/capital-pools/pool-alpha-1/status",
        json={"actor_id": "admin-alpha", "actor_role": "capital.admin", "status": "suspended"},
        headers=headers_a,
    )
    assert res.status_code == 200
    assert res.json()["status"] == "suspended"
    # Transition back to active
    client.patch(
        "/api/capital-pools/pool-alpha-1/status",
        json={"actor_id": "admin-alpha", "actor_role": "capital.admin", "status": "active"},
        headers=headers_a,
    )

    # 5. GET /api/capital-pools/{pool_id}/live-owner
    route_5 = "GET /api/capital-pools/{pool_id}/live-owner"
    TESTED_ROUTES.add(route_5)
    res = client.get("/api/capital-pools/pool-alpha-1/live-owner", headers=headers_a)
    assert res.status_code == 200
    assert res.headers["X-Pantheon-Tenant"] == "tenant-alpha"
    res_cross = client.get("/api/capital-pools/pool-alpha-1/live-owner", headers=headers_b)
    assert res_cross.status_code == 404

    # 6. POST /api/bindings
    route_6 = "POST /api/bindings"
    TESTED_ROUTES.add(route_6)
    # Cross-tenant binding attempt to alpha pool
    res_cross = client.post(
        "/api/bindings",
        json={
            "actor_id": "admin-beta",
            "actor_role": "persona.admin",
            "binding_id": "binding-beta-cross",
            "persona_id": "persona-beta",
            "capital_pool_id": "pool-alpha-1",
            "capital_sleeve_id": "sleeve-1",
            "role": "live_owner",
            "allowed_deployment_scope": "paper",
        },
        headers=headers_b,
    )
    assert res_cross.status_code == 404

    res = client.post(
        "/api/bindings",
        json={
            "actor_id": "admin-alpha",
            "actor_role": "persona.admin",
            "binding_id": "binding-alpha-1",
            "persona_id": "persona-alpha",
            "capital_pool_id": "pool-alpha-1",
            "capital_sleeve_id": "sleeve-1",
            "role": "live_owner",
            "allowed_deployment_scope": "canary",
        },
        headers=headers_a,
    )
    assert res.status_code == 201, res.text
    assert res.headers["X-Pantheon-Tenant"] == "tenant-alpha"
    assert res.json()["tenant_id"] == "tenant-alpha"

    # 7. GET /api/bindings
    route_7 = "GET /api/bindings"
    TESTED_ROUTES.add(route_7)
    res = client.get("/api/bindings", headers=headers_a)
    assert res.status_code == 200
    assert "binding-alpha-1" in [b["binding_id"] for b in res.json()]
    res_b = client.get("/api/bindings", headers=headers_b)
    assert res_b.status_code == 200
    assert "binding-alpha-1" not in [b["binding_id"] for b in res_b.json()]

    # 8. GET /api/bindings/{binding_id}
    route_8 = "GET /api/bindings/{binding_id}"
    TESTED_ROUTES.add(route_8)
    res = client.get("/api/bindings/binding-alpha-1", headers=headers_a)
    assert res.status_code == 200
    assert res.json()["binding_id"] == "binding-alpha-1"
    assert res.json()["tenant_id"] == "tenant-alpha"
    res_cross = client.get("/api/bindings/binding-alpha-1", headers=headers_b)
    assert res_cross.status_code == 404

    # 9. POST /api/bindings/{binding_id}/activate
    route_9 = "POST /api/bindings/{binding_id}/activate"
    TESTED_ROUTES.add(route_9)
    res_cross = client.post(
        "/api/bindings/binding-alpha-1/activate",
        json={"actor_id": "admin-beta", "actor_role": "persona.admin", "approval_decision_id": "app-001"},
        headers=headers_b,
    )
    assert res_cross.status_code == 404
    res = client.post(
        "/api/bindings/binding-alpha-1/activate",
        json={"actor_id": "admin-alpha", "actor_role": "persona.admin", "approval_decision_id": "app-001"},
        headers=headers_a,
    )
    assert res.status_code == 200
    assert res.json()["status"] == "active"

    # 10. PATCH /api/bindings/{binding_id}/status
    route_10 = "PATCH /api/bindings/{binding_id}/status"
    TESTED_ROUTES.add(route_10)
    res_cross = client.patch(
        "/api/bindings/binding-alpha-1/status",
        json={"actor_id": "admin-beta", "actor_role": "persona.admin", "status": "suspended"},
        headers=headers_b,
    )
    assert res_cross.status_code == 404
    res = client.patch(
        "/api/bindings/binding-alpha-1/status",
        json={"actor_id": "admin-alpha", "actor_role": "persona.admin", "status": "suspended"},
        headers=headers_a,
    )
    assert res.status_code == 200
    assert res.json()["status"] == "suspended"
    # Reactivate for subsequent rebalance testing
    client.post(
        "/api/bindings/binding-alpha-1/activate",
        json={"actor_id": "admin-alpha", "actor_role": "persona.admin", "approval_decision_id": "app-002"},
        headers=headers_a,
    )

    # 11. GET /api/bindings/admissibility
    route_11 = "GET /api/bindings/admissibility"
    TESTED_ROUTES.add(route_11)
    res = client.get(
        "/api/bindings/admissibility",
        params={"persona_id": "persona-alpha", "capital_pool_id": "pool-alpha-1", "target_stage": "canary"},
        headers=headers_a,
    )
    assert res.status_code == 200
    assert res.json()["permitted"] is True

    res_cross = client.get(
        "/api/bindings/admissibility",
        params={"persona_id": "persona-alpha", "capital_pool_id": "pool-alpha-1", "target_stage": "canary"},
        headers=headers_b,
    )
    assert res_cross.status_code == 404

    # 12. POST /api/rebalances
    route_12 = "POST /api/rebalances"
    TESTED_ROUTES.add(route_12)
    rb_line = {
        "ranking_snapshot_id": "snap-1",
        "allocation_evaluation_id": "eval-1",
        "allocation_policy_version": "v1",
        "persona_id": "persona-alpha",
        "capital_sleeve_id": "sleeve-1",
        "target_weight": 0.5,
        "current_weight": 0.0,
        "capital_scope": "sleeve",
        "stage": "canary",
    }
    rb_line["allocation_line_digest"] = allocation_line_digest(rb_line)
    rebalance_payload = {
        "actor_id": "admin-alpha",
        "actor_role": "operator",
        "rebalance_id": "rb-alpha-1",
        "capital_pool_id": "pool-alpha-1",
        "ranking_snapshot_id": "snap-1",
        "allocation_evaluation_id": "eval-1",
        "allocation_policy_version": "v1",
        "request_hash": "hash-alpha-1",
        "idempotency_key": "idem-alpha-1",
        "lines": [rb_line],
    }
    # Cross tenant cannot create rebalance for alpha pool
    res_cross = client.post(
        "/api/rebalances",
        json={**rebalance_payload, "actor_id": "admin-beta"},
        headers=headers_b,
    )
    assert res_cross.status_code == 404

    res = client.post("/api/rebalances", json=rebalance_payload, headers=headers_a)
    assert res.status_code == 201, res.text
    assert res.headers["X-Pantheon-Tenant"] == "tenant-alpha"
    assert res.json()["tenant_id"] == "tenant-alpha"

    # 13. GET /api/rebalances
    route_13 = "GET /api/rebalances"
    TESTED_ROUTES.add(route_13)
    res = client.get("/api/rebalances", headers=headers_a)
    assert res.status_code == 200
    assert "rb-alpha-1" in [r["rebalance_id"] for r in res.json()]
    res_b = client.get("/api/rebalances", headers=headers_b)
    assert res_b.status_code == 200
    assert "rb-alpha-1" not in [r["rebalance_id"] for r in res_b.json()]

    # 14. GET /api/rebalances/{rebalance_id}
    route_14 = "GET /api/rebalances/{rebalance_id}"
    TESTED_ROUTES.add(route_14)
    res = client.get("/api/rebalances/rb-alpha-1", headers=headers_a)
    assert res.status_code == 200
    assert res.json()["tenant_id"] == "tenant-alpha"
    res_cross = client.get("/api/rebalances/rb-alpha-1", headers=headers_b)
    assert res_cross.status_code == 404

    # 15. POST /api/rebalances/{rebalance_id}/apply
    route_15 = "POST /api/rebalances/{rebalance_id}/apply"
    TESTED_ROUTES.add(route_15)
    apply_payload = {
        "actor_id": "admin-alpha",
        "actor_role": "operator",
        "command_id": "cmd-alpha-apply-1",
        "rebalance_id": "rb-alpha-1",
        "request_hash": "hash-alpha-apply",
        "idempotency_key": "idem-alpha-apply",
        "approval_ref": "approval-apply-1",
    }
    res_cross = client.post(
        "/api/rebalances/rb-alpha-1/apply",
        json={**apply_payload, "actor_id": "admin-beta"},
        headers=headers_b,
    )
    assert res_cross.status_code == 404

    res = client.post("/api/rebalances/rb-alpha-1/apply", json=apply_payload, headers=headers_a)
    assert res.status_code == 200
    assert res.headers["X-Pantheon-Tenant"] == "tenant-alpha"
    assert res.json()["tenant_id"] == "tenant-alpha"

    # 16. GET /api/rebalances/receipts/{command_id}
    route_16 = "GET /api/rebalances/receipts/{command_id}"
    TESTED_ROUTES.add(route_16)
    res = client.get("/api/rebalances/receipts/cmd-alpha-apply-1", headers=headers_a)
    assert res.status_code == 200
    assert res.json()["tenant_id"] == "tenant-alpha"
    res_cross = client.get("/api/rebalances/receipts/cmd-alpha-apply-1", headers=headers_b)
    assert res_cross.status_code == 404

    # 17. GET /api/allocations
    route_17 = "GET /api/allocations"
    TESTED_ROUTES.add(route_17)
    res = client.get("/api/allocations", headers=headers_a)
    assert res.status_code == 200
    assert len(res.json()["items"]) > 0
    res_b = client.get("/api/allocations", headers=headers_b)
    assert res_b.status_code == 200
    assert len(res_b.json()["items"]) == 0

    # 18. GET /api/capital-pools/{pool_id}/allocations
    route_18 = "GET /api/capital-pools/{pool_id}/allocations"
    TESTED_ROUTES.add(route_18)
    res = client.get("/api/capital-pools/pool-alpha-1/allocations", headers=headers_a)
    assert res.status_code == 200
    assert len(res.json()["items"]) > 0
    res_cross = client.get("/api/capital-pools/pool-alpha-1/allocations", headers=headers_b)
    assert res_cross.status_code == 404

    # 19. POST /api/containments
    route_19 = "POST /api/containments"
    TESTED_ROUTES.add(route_19)
    containment_payload = {
        "actor_id": "admin-alpha",
        "actor_role": "operator",
        "command_id": "cmd-cont-alpha-1",
        "persona_id": "persona-alpha",
        "capital_pool_id": "pool-alpha-1",
        "action": "freeze",
        "trigger": "manual_override",
        "evidence_refs": ["ev-1"],
        "reason": "risk containment",
        "request_hash": "hash-cont-1",
        "idempotency_key": "idem-cont-1",
    }
    res_cross = client.post(
        "/api/containments",
        json={**containment_payload, "actor_id": "admin-beta"},
        headers=headers_b,
    )
    assert res_cross.status_code == 404

    res = client.post("/api/containments", json=containment_payload, headers=headers_a)
    assert res.status_code == 201
    assert res.headers["X-Pantheon-Tenant"] == "tenant-alpha"
    assert res.json()["tenant_id"] == "tenant-alpha"

    # 20. GET /api/containments
    route_20 = "GET /api/containments"
    TESTED_ROUTES.add(route_20)
    res = client.get("/api/containments", headers=headers_a)
    assert res.status_code == 200
    assert len(res.json()) > 0
    assert res.json()[0]["tenant_id"] == "tenant-alpha"
    res_b = client.get("/api/containments", headers=headers_b)
    assert res_b.status_code == 200
    assert len(res_b.json()) == 0

    # 21. GET /api/containments/receipts/{command_id}
    route_21 = "GET /api/containments/receipts/{command_id}"
    TESTED_ROUTES.add(route_21)
    res = client.get("/api/containments/receipts/cmd-cont-alpha-1", headers=headers_a)
    assert res.status_code == 200
    assert res.json()["tenant_id"] == "tenant-alpha"
    res_cross = client.get("/api/containments/receipts/cmd-cont-alpha-1", headers=headers_b)
    assert res_cross.status_code == 404

    # 22. GET /api/capital/write-authority
    route_22 = "GET /api/capital/write-authority"
    TESTED_ROUTES.add(route_22)
    res_a = client.get("/api/capital/write-authority", headers=headers_a)
    assert res_a.status_code == 200
    assert res_a.headers["X-Pantheon-Tenant"] == "tenant-alpha"
    res_b = client.get("/api/capital/write-authority", headers=headers_b)
    assert res_b.status_code == 200
    assert res_b.headers["X-Pantheon-Tenant"] == "tenant-beta"

    # 23. GET /api/capital/audit
    route_23 = "GET /api/capital/audit"
    TESTED_ROUTES.add(route_23)
    res_a = client.get("/api/capital/audit", headers=headers_a)
    assert res_a.status_code == 200
    events_a = res_a.json()
    assert len(events_a) > 0
    assert all(e.get("tenant_id") == "tenant-alpha" for e in events_a)

    res_b = client.get("/api/capital/audit", headers=headers_b)
    assert res_b.status_code == 200
    events_b = res_b.json()
    assert all(e.get("tenant_id") == "tenant-beta" for e in events_b)

    # Verify all 23 routes were explicitly tested
    assert len(TESTED_ROUTES) == 23, f"Expected 23 routes tested, got {len(TESTED_ROUTES)}: {TESTED_ROUTES}"


def test_untenanted_rows_visible_to_no_caller(capital_test_env):
    client, module, tempdir = capital_test_env
    headers_a = _auth_headers("tenant-alpha")
    headers_b = _auth_headers("tenant-beta")

    # Manually inject rows without tenant into storage files
    pool_store_path = tempdir / "capital_pools.json"
    binding_store_path = tempdir / "persona_capital_bindings.json"
    alloc_path = tempdir / "capital_allocation_authority.json"
    audit_path = tempdir / "capital_audit.jsonl"

    untenanted_pool = {
        "pool_id": "pool-no-tenant",
        "name": "Untenanted Pool",
        "owner_id": "fund-untenanted",
        "owner_type": "fund",
        "status": "active",
        "created_at": "2026-01-01T00:00:00Z",
        "metadata": {},  # No tenant_id
    }
    pool_store_path.write_text(json.dumps([untenanted_pool]), encoding="utf-8")

    untenanted_binding = {
        "binding_id": "binding-no-tenant",
        "persona_id": "persona-ghost",
        "capital_pool_id": "pool-no-tenant",
        "role": "live_owner",
        "allowed_deployment_scope": "paper",
        "status": "active",
        "approval_decision_id": "app-untenanted",
        "created_at": "2026-01-01T00:00:00Z",
        "metadata": {},  # No tenant_id
    }
    binding_store_path.write_text(json.dumps([untenanted_binding]), encoding="utf-8")

    untenanted_alloc = {
        "rebalances": {
            "rb-ghost": {
                "rebalance_id": "rb-ghost",
                "capital_pool_id": "pool-no-tenant",
                "status": "applied",
                "created_at": "2026-01-01T00:00:00Z",
                # No tenant_id
            }
        },
        "allocations": {
            "alloc-ghost": {
                "allocation_id": "alloc-ghost",
                "capital_pool_id": "pool-no-tenant",
                "current_weight": 0.5,
                # No tenant_id
            }
        },
        "containments": {
            "cont-ghost": {
                "containment_id": "cont-ghost",
                "action": "freeze",
                "persona_id": "persona-ghost",
                # No tenant_id
            }
        },
        "command_receipts": {},
        "containment_commands": {},
        "idempotency": {},
    }
    alloc_path.write_text(json.dumps(untenanted_alloc), encoding="utf-8")

    untenanted_event = {
        "event_id": "evt-ghost",
        "event_type": "capital_pool_created",
        "resource_type": "CapitalPool",
        "resource_id": "pool-no-tenant",
        # No tenant_id
    }
    audit_path.write_text(json.dumps(untenanted_event) + "\n", encoding="utf-8")

    # Reload store in service
    service = module.get_capital_service()
    service.pool_store._load(service.pool_store._path)
    service.binding_store._load(service.binding_store._path)

    # Neither Tenant A nor Tenant B can see the untenanted rows
    for headers in (headers_a, headers_b):
        # Pool listing and get
        pools = client.get("/api/capital-pools", headers=headers).json()
        assert "pool-no-tenant" not in [p["pool_id"] for p in pools]
        assert client.get("/api/capital-pools/pool-no-tenant", headers=headers).status_code == 404

        # Binding listing and get
        bindings = client.get("/api/bindings", headers=headers).json()
        assert "binding-no-tenant" not in [b["binding_id"] for b in bindings]
        assert client.get("/api/bindings/binding-no-tenant", headers=headers).status_code == 404

        # Rebalances
        rebalances = client.get("/api/rebalances", headers=headers).json()
        assert "rb-ghost" not in [r["rebalance_id"] for r in rebalances]
        assert client.get("/api/rebalances/rb-ghost", headers=headers).status_code == 404

        # Allocations
        allocations = client.get("/api/allocations", headers=headers).json()["items"]
        assert "alloc-ghost" not in [a["allocation_id"] for a in allocations]

        # Containments
        containments = client.get("/api/containments", headers=headers).json()
        assert "cont-ghost" not in [c["containment_id"] for c in containments]

        # Audit events
        audit_events = client.get("/api/capital/audit", headers=headers).json()
        assert "evt-ghost" not in [e.get("event_id") for e in audit_events]


def test_backfill_and_migration(capital_test_env):
    client, module, tempdir = capital_test_env
    alloc_store = module.allocation_authority_store

    # Test backfill_tenant on allocation store
    alloc_store._data["rebalances"]["rb-old"] = {
        "rebalance_id": "rb-old",
        "capital_pool_id": "pool-old",
        "status": "applied",
    }
    alloc_store._persist_locked()
    # Initially invisible
    with pytest.raises(module.AllocationAuthorityNotFound):
        alloc_store.get_rebalance("rb-old", tenant_id="tenant-restored")

    # Run backfill
    alloc_store.backfill_tenant(default_tenant="tenant-restored")

    # Now visible to restored tenant, but invisible to other tenant
    rb = alloc_store.get_rebalance("rb-old", tenant_id="tenant-restored")
    assert rb["tenant_id"] == "tenant-restored"
    with pytest.raises(module.AllocationAuthorityNotFound):
        alloc_store.get_rebalance("rb-old", tenant_id="tenant-other")


def test_cross_tenant_pool_idempotency(capital_test_env):
    client, module, _ = capital_test_env
    payload = {
        "actor_id": "shared-actor",
        "actor_role": "capital.admin",
        "pool_id": "pool-replay",
        "name": "Private alpha pool",
        "owner_id": "fund",
        "owner_type": "fund",
        "idempotency_key": "same-key",
        "request_hash": "same-hash",
    }
    first = client.post("/api/capital-pools", json=payload, headers=_auth_headers("tenant-alpha", actor_id="shared-actor"))
    assert first.status_code == 201, first.text
    second = client.post("/api/capital-pools", json=payload, headers=_auth_headers("tenant-beta", actor_id="shared-actor"))
    assert second.status_code >= 400 or second.json().get("tenant_id") == "tenant-beta", "Tenant beta received tenant alpha pool on idempotent create"


def test_containment_idempotent_replay_tenant_scoping(capital_test_env):
    client, module, _ = capital_test_env
    headers_a = _auth_headers("tenant-alpha", actor_id="admin-alpha")
    payload = {
        "persona_id": "persona-p1",
        "action": "freeze",
        "trigger": "manual_override",
        "evidence_refs": ["ev-1"],
        "actor_id": "admin-alpha",
        "actor_role": "operator",
        "idempotency_key": "idem-cont-replay-1",
        "request_hash": "req-cont-replay-1",
    }
    # Initial create succeeds and returns tenant-alpha
    res = client.post("/api/containments", json=payload, headers=headers_a)
    assert res.status_code == 201, res.text
    data = res.json()
    cont_id = data["containment_id"]
    cmd_id = data["command_id"]
    assert data["tenant_id"] == "tenant-alpha"
    assert data["idempotent_replay"] is False

    # Same-tenant replay succeeds with idempotent_replay=True
    same_replay = client.post("/api/containments", json=payload, headers=headers_a)
    assert same_replay.status_code == 201
    assert same_replay.json()["idempotent_replay"] is True
    assert same_replay.json()["tenant_id"] == "tenant-alpha"

    # Injected null tenant in stored containment hides it from GET and fails idempotent replay
    store = module.allocation_authority_store
    store._data["containments"][cont_id]["tenant_id"] = None
    store._persist_locked()
    assert client.get(f"/api/containments/receipts/{cmd_id}", headers=headers_a).status_code == 404
    replay_null = client.post("/api/containments", json=payload, headers=headers_a)
    assert replay_null.status_code == 404

    # Injected foreign tenant in stored containment hides it from GET and fails idempotent replay
    store._data["containments"][cont_id]["tenant_id"] = "tenant-beta"
    store._persist_locked()
    assert client.get(f"/api/containments/receipts/{cmd_id}", headers=headers_a).status_code == 404
    replay_foreign = client.post("/api/containments", json=payload, headers=headers_a)
    assert replay_foreign.status_code == 404


def test_rebalance_idempotent_replay_tenant_scoping(capital_test_env):
    client, module, _ = capital_test_env
    headers_a = _auth_headers("tenant-alpha", actor_id="admin-alpha")
    client.post("/api/capital-pools", json={
        "actor_id": "admin-alpha",
        "actor_role": "capital.admin",
        "pool_id": "pool-replay-test",
        "name": "Pool Replay Test",
        "owner_id": "fund-1",
        "owner_type": "fund",
    }, headers=headers_a)
    client.post("/api/bindings", json={
        "actor_id": "admin-alpha",
        "actor_role": "persona.admin",
        "binding_id": "bind-replay-test",
        "persona_id": "persona-replay-test",
        "capital_pool_id": "pool-replay-test",
        "capital_sleeve_id": "sleeve-1",
        "role": "live_owner",
        "allowed_deployment_scope": "canary",
    }, headers=headers_a)
    client.post("/api/bindings/bind-replay-test/activate", json={
        "actor_id": "admin-alpha",
        "actor_role": "persona.admin",
        "approval_decision_id": "app-replay-test",
    }, headers=headers_a)

    rb_line = {
        "capital_pool_id": "pool-replay-test",
        "ranking_snapshot_id": "snap-1",
        "allocation_evaluation_id": "eval-1",
        "allocation_policy_version": "v1",
        "persona_id": "persona-replay-test",
        "capital_sleeve_id": "sleeve-1",
        "target_weight": 0.5,
        "current_weight": 0.0,
        "capital_scope": "sleeve",
        "stage": "canary",
    }
    rb_line["allocation_line_digest"] = allocation_line_digest(rb_line)
    rb_payload = {
        "actor_id": "admin-alpha",
        "actor_role": "operator",
        "rebalance_id": "rb-replay-test",
        "capital_pool_id": "pool-replay-test",
        "ranking_snapshot_id": "snap-1",
        "allocation_evaluation_id": "eval-1",
        "allocation_policy_version": "v1",
        "request_hash": "req-rb-replay-1",
        "idempotency_key": "idem-rb-replay-1",
        "lines": [rb_line],
    }
    res = client.post("/api/rebalances", json=rb_payload, headers=headers_a)
    assert res.status_code == 201, res.text
    rb_id = res.json()["rebalance_id"]

    # Same tenant rebalance create replay succeeds
    same_rb = client.post("/api/rebalances", json=rb_payload, headers=headers_a)
    assert same_rb.status_code == 201
    assert same_rb.json()["rebalance_id"] == rb_id

    store = module.allocation_authority_store
    # Untenanted / foreign tenant rebalance create replay fails
    store._data["rebalances"][rb_id]["tenant_id"] = None
    store._persist_locked()
    assert client.get(f"/api/rebalances/{rb_id}", headers=headers_a).status_code == 404
    assert client.post("/api/rebalances", json=rb_payload, headers=headers_a).status_code == 404

    store._data["rebalances"][rb_id]["tenant_id"] = "tenant-beta"
    store._persist_locked()
    assert client.get(f"/api/rebalances/{rb_id}", headers=headers_a).status_code == 404
    assert client.post("/api/rebalances", json=rb_payload, headers=headers_a).status_code == 404

    # Restore rebalance tenant and apply
    store._data["rebalances"][rb_id]["tenant_id"] = "tenant-alpha"
    store._persist_locked()

    apply_payload = {
        "actor_id": "admin-alpha",
        "actor_role": "operator",
        "command_id": "cmd-apply-replay-1",
        "rebalance_id": rb_id,
        "request_hash": "hash-apply-replay-1",
        "idempotency_key": "idem-apply-replay-1",
        "approval_ref": "appr-replay-1",
    }
    res_apply = client.post(f"/api/rebalances/{rb_id}/apply", json=apply_payload, headers=headers_a)
    assert res_apply.status_code == 200, res_apply.text
    assert res_apply.json()["idempotent_replay"] is False

    # Same tenant apply replay succeeds
    same_apply = client.post(f"/api/rebalances/{rb_id}/apply", json=apply_payload, headers=headers_a)
    assert same_apply.status_code == 200
    assert same_apply.json()["idempotent_replay"] is True

    # Untenanted / foreign tenant apply replay fails
    store._data["command_receipts"]["cmd-apply-replay-1"]["tenant_id"] = None
    store._persist_locked()
    assert client.get("/api/rebalances/receipts/cmd-apply-replay-1", headers=headers_a).status_code == 404
    assert client.post(f"/api/rebalances/{rb_id}/apply", json=apply_payload, headers=headers_a).status_code == 404

    store._data["command_receipts"]["cmd-apply-replay-1"]["tenant_id"] = "tenant-beta"
    store._persist_locked()
    assert client.get("/api/rebalances/receipts/cmd-apply-replay-1", headers=headers_a).status_code == 404
    assert client.post(f"/api/rebalances/{rb_id}/apply", json=apply_payload, headers=headers_a).status_code == 404


def test_untenanted_live_owner_hidden(capital_test_env):
    client, module, tempdir = capital_test_env
    headers = _auth_headers("tenant-alpha", actor_id="admin-alpha")
    created = client.post("/api/capital-pools", json={"actor_id": "admin-alpha", "actor_role": "capital.admin", "pool_id": "pool-a", "name": "A", "owner_id": "fund", "owner_type": "fund"}, headers=headers)
    assert created.status_code == 201, created.text
    binding = {"binding_id": "legacy-untenanted", "persona_id": "legacy-persona", "capital_pool_id": "pool-a", "role": "live_owner", "allowed_deployment_scope": "live", "status": "active", "approval_decision_id": "app", "created_at": "2026-01-01T00:00:00Z", "metadata": {}}
    (tempdir / "persona_capital_bindings.json").write_text(json.dumps([binding]))
    module.binding_store._load(module.binding_store._path)
    assert client.get("/api/bindings/legacy-untenanted", headers=headers).status_code == 404
    response = client.get("/api/capital-pools/pool-a/live-owner", headers=headers)
    assert response.json() is None, "Untenanted binding leaked through live-owner endpoint"

    # 1. Probe 1 (activate conflict): POST new live_owner binding succeeds, activate fails without disclosing hidden binding id
    create_lo = client.post(
        "/api/bindings",
        json={
            "actor_id": "admin-alpha",
            "actor_role": "persona.admin",
            "binding_id": "alpha-new-binding",
            "persona_id": "persona-alpha",
            "capital_pool_id": "pool-a",
            "role": "live_owner",
            "allowed_deployment_scope": "live",
        },
        headers=headers,
    )
    assert create_lo.status_code == 201, create_lo.text
    act_lo = client.post(
        "/api/bindings/alpha-new-binding/activate",
        json={"actor_id": "admin-alpha", "actor_role": "persona.admin", "approval_decision_id": "app-alpha"},
        headers=headers,
    )
    assert act_lo.status_code == 400
    assert "Single-live-owner rule" in act_lo.json()["detail"]
    assert "legacy-untenanted" not in act_lo.text

    # 2. Probe 2 (create conflict): Assign hidden fixture binding capital_sleeve_id=sleeve-a; POST new binding for sleeve-a returns 400 without disclosing hidden binding id
    binding["capital_sleeve_id"] = "sleeve-a"
    (tempdir / "persona_capital_bindings.json").write_text(json.dumps([binding]))
    module.binding_store._load(module.binding_store._path)

    create_sleeve = client.post(
        "/api/bindings",
        json={
            "actor_id": "admin-alpha",
            "actor_role": "persona.admin",
            "binding_id": "alpha-sleeve-binding",
            "persona_id": "persona-alpha-2",
            "capital_pool_id": "pool-a",
            "capital_sleeve_id": "sleeve-a",
            "role": "advisor",
            "allowed_deployment_scope": "none",
        },
        headers=headers,
    )
    assert create_sleeve.status_code == 400
    assert "already bound" in create_sleeve.json()["detail"]
    assert "legacy-untenanted" not in create_sleeve.text


def test_foreign_and_same_tenant_binding_conflict_redaction(capital_test_env):
    client, module, tempdir = capital_test_env
    headers_a = _auth_headers("tenant-alpha", actor_id="admin-alpha")

    created_a = client.post(
        "/api/capital-pools",
        json={"actor_id": "admin-alpha", "actor_role": "capital.admin", "pool_id": "pool-x", "name": "X", "owner_id": "fund-x", "owner_type": "fund"},
        headers=headers_a,
    )
    assert created_a.status_code == 201

    # 1. Foreign-tenant live_owner conflict with parentheses and newlines on activate:
    for foreign_id in ["beta)live-owner", "beta\nlive-owner", "beta)live\nowner"]:
        foreign_binding = {
            "binding_id": foreign_id,
            "tenant_id": "tenant-beta",
            "persona_id": "persona-beta",
            "capital_pool_id": "pool-x",
            "role": "live_owner",
            "allowed_deployment_scope": "live",
            "status": "active",
            "approval_decision_id": "app-b",
            "created_at": "2026-01-01T00:00:00Z",
            "metadata": {"tenant_id": "tenant-beta"},
        }
        (tempdir / "persona_capital_bindings.json").write_text(json.dumps([foreign_binding]))
        module.binding_store._bindings.clear()
        module.binding_store._load(module.binding_store._path)

        create_lo = client.post(
            "/api/bindings",
            json={
                "actor_id": "admin-alpha",
                "actor_role": "persona.admin",
                "binding_id": "alpha-lo-foreign-test",
                "persona_id": "persona-alpha",
                "capital_pool_id": "pool-x",
                "role": "live_owner",
                "allowed_deployment_scope": "live",
            },
            headers=headers_a,
        )
        assert create_lo.status_code == 201
        act_lo = client.post(
            "/api/bindings/alpha-lo-foreign-test/activate",
            json={"actor_id": "admin-alpha", "actor_role": "persona.admin", "approval_decision_id": "app-a"},
            headers=headers_a,
        )
        assert act_lo.status_code == 400
        assert "Single-live-owner rule" in act_lo.json()["detail"]
        assert foreign_id not in act_lo.text
        assert "tenant-beta" not in act_lo.text

    # 2. Null-tenant live_owner conflict with parentheses and newlines:
    for null_id in ["null)live-owner", "null\nlive-owner", "null)live\nowner"]:
        null_binding = {
            "binding_id": null_id,
            "persona_id": "persona-null",
            "capital_pool_id": "pool-x",
            "role": "live_owner",
            "allowed_deployment_scope": "live",
            "status": "active",
            "approval_decision_id": "app-null",
            "created_at": "2026-01-01T00:00:00Z",
            "metadata": {},
        }
        (tempdir / "persona_capital_bindings.json").write_text(json.dumps([null_binding]))
        module.binding_store._bindings.clear()
        module.binding_store._load(module.binding_store._path)

        create_lo = client.post(
            "/api/bindings",
            json={
                "actor_id": "admin-alpha",
                "actor_role": "persona.admin",
                "binding_id": "alpha-lo-null-test",
                "persona_id": "persona-alpha",
                "capital_pool_id": "pool-x",
                "role": "live_owner",
                "allowed_deployment_scope": "live",
            },
            headers=headers_a,
        )
        assert create_lo.status_code == 201
        act_lo = client.post(
            "/api/bindings/alpha-lo-null-test/activate",
            json={"actor_id": "admin-alpha", "actor_role": "persona.admin", "approval_decision_id": "app-a"},
            headers=headers_a,
        )
        assert act_lo.status_code == 400
        assert "Single-live-owner rule" in act_lo.json()["detail"]
        assert null_id not in act_lo.text

    # 3. Same-tenant live_owner conflict with parentheses and newlines preserves disclosure:
    for same_lo_id in ["alpha)live-owner", "alpha\nlive-owner", "alpha)live\nowner"]:
        same_lo = {
            "binding_id": same_lo_id,
            "tenant_id": "tenant-alpha",
            "persona_id": "persona-alpha-lo",
            "capital_pool_id": "pool-x",
            "role": "live_owner",
            "allowed_deployment_scope": "live",
            "status": "active",
            "approval_decision_id": "app-same",
            "created_at": "2026-01-01T00:00:00Z",
            "metadata": {"tenant_id": "tenant-alpha"},
        }
        (tempdir / "persona_capital_bindings.json").write_text(json.dumps([same_lo]))
        module.binding_store._bindings.clear()
        module.binding_store._load(module.binding_store._path)

        create_lo = client.post(
            "/api/bindings",
            json={
                "actor_id": "admin-alpha",
                "actor_role": "persona.admin",
                "binding_id": "alpha-lo-same-test",
                "persona_id": "persona-alpha-lo-2",
                "capital_pool_id": "pool-x",
                "role": "live_owner",
                "allowed_deployment_scope": "live",
            },
            headers=headers_a,
        )
        assert create_lo.status_code == 201
        act_lo = client.post(
            "/api/bindings/alpha-lo-same-test/activate",
            json={"actor_id": "admin-alpha", "actor_role": "persona.admin", "approval_decision_id": "app-a"},
            headers=headers_a,
        )
        assert act_lo.status_code == 400
        assert "Single-live-owner rule" in act_lo.json()["detail"]
        assert same_lo_id in act_lo.json()["detail"]

    # 4. Foreign-tenant sleeve conflict with quotes, parentheses, and newlines:
    for foreign_sleeve_id in ["beta)sleeve", "beta'sleeve\"bound", "beta\nsleeve\nbound"]:
        foreign_sleeve = {
            "binding_id": foreign_sleeve_id,
            "tenant_id": "tenant-beta",
            "persona_id": "persona-beta-2",
            "capital_pool_id": "pool-x",
            "capital_sleeve_id": "sleeve-foreign",
            "role": "advisor",
            "allowed_deployment_scope": "none",
            "status": "pending",
            "created_at": "2026-01-01T00:00:00Z",
            "metadata": {"tenant_id": "tenant-beta"},
        }
        (tempdir / "persona_capital_bindings.json").write_text(json.dumps([foreign_sleeve]))
        module.binding_store._bindings.clear()
        module.binding_store._load(module.binding_store._path)

        create_sleeve = client.post(
            "/api/bindings",
            json={
                "actor_id": "admin-alpha",
                "actor_role": "persona.admin",
                "binding_id": "alpha-sleeve-foreign-test",
                "persona_id": "persona-alpha-3",
                "capital_pool_id": "pool-x",
                "capital_sleeve_id": "sleeve-foreign",
                "role": "advisor",
                "allowed_deployment_scope": "none",
            },
            headers=headers_a,
        )
        assert create_sleeve.status_code == 400
        assert "already bound" in create_sleeve.json()["detail"]
        assert foreign_sleeve_id not in create_sleeve.text
        assert "tenant-beta" not in create_sleeve.text

    # 5. Null-tenant sleeve conflict with quotes, parentheses, and newlines:
    for null_sleeve_id in ["null)sleeve", "null'sleeve\"bound", "null\nsleeve\nbound"]:
        null_sleeve = {
            "binding_id": null_sleeve_id,
            "persona_id": "persona-null-2",
            "capital_pool_id": "pool-x",
            "capital_sleeve_id": "sleeve-null",
            "role": "advisor",
            "allowed_deployment_scope": "none",
            "status": "pending",
            "created_at": "2026-01-01T00:00:00Z",
            "metadata": {},
        }
        (tempdir / "persona_capital_bindings.json").write_text(json.dumps([null_sleeve]))
        module.binding_store._bindings.clear()
        module.binding_store._load(module.binding_store._path)

        create_sleeve = client.post(
            "/api/bindings",
            json={
                "actor_id": "admin-alpha",
                "actor_role": "persona.admin",
                "binding_id": "alpha-sleeve-null-test",
                "persona_id": "persona-alpha-3",
                "capital_pool_id": "pool-x",
                "capital_sleeve_id": "sleeve-null",
                "role": "advisor",
                "allowed_deployment_scope": "none",
            },
            headers=headers_a,
        )
        assert create_sleeve.status_code == 400
        assert "already bound" in create_sleeve.json()["detail"]
        assert null_sleeve_id not in create_sleeve.text

    # 6. Same-tenant sleeve conflict with quotes, parentheses, and newlines preserves disclosure:
    for same_sleeve_id in ["alpha)sleeve", "alpha'sleeve\"bound", "alpha\nsleeve\nbound"]:
        same_binding = {
            "binding_id": same_sleeve_id,
            "tenant_id": "tenant-alpha",
            "persona_id": "persona-alpha-4",
            "capital_pool_id": "pool-x",
            "capital_sleeve_id": "sleeve-same",
            "role": "advisor",
            "allowed_deployment_scope": "none",
            "status": "pending",
            "created_at": "2026-01-01T00:00:00Z",
            "metadata": {"tenant_id": "tenant-alpha"},
        }
        (tempdir / "persona_capital_bindings.json").write_text(json.dumps([same_binding]))
        module.binding_store._bindings.clear()
        module.binding_store._load(module.binding_store._path)

        create_same = client.post(
            "/api/bindings",
            json={
                "actor_id": "admin-alpha",
                "actor_role": "persona.admin",
                "binding_id": "alpha-sleeve-same-test",
                "persona_id": "persona-alpha-5",
                "capital_pool_id": "pool-x",
                "capital_sleeve_id": "sleeve-same",
                "role": "advisor",
                "allowed_deployment_scope": "none",
            },
            headers=headers_a,
        )
        assert create_same.status_code == 400
        assert "already bound" in create_same.json()["detail"]
        assert f"binding={same_sleeve_id!r}" in create_same.json()["detail"]


def test_postgres_migration_and_write_path(monkeypatch):
    import sys
    from services.capital.pg_store import (
        PostgresAllocationAuthorityStore,
        migrate_capital_tables,
    )

    # 1. Missing psycopg fails closed with RuntimeError
    monkeypatch.setitem(sys.modules, "psycopg", None)
    with pytest.raises(RuntimeError, match="psycopg is required"):
        migrate_capital_tables("postgresql://user:pass@localhost:5432/db")

    # 2. Fake psycopg to test DDL, index quoting, backfill, and persistence
    class FakeCursor:
        def __init__(self, rows=None):
            self.rows = rows or []

        def fetchall(self):
            return self.rows

        def fetchone(self):
            return self.rows[0] if self.rows else None

    class FakeConn:
        def __init__(self):
            self.executed_statements = []
            self.tables = {
                "capital.capital_pools": [
                    ("p1", {"name": "Pool 1", "metadata": {}}, None),
                ],
                "capital.persona_capital_bindings": [
                    ("b1", {"binding_id": "b1", "metadata": {}}, None),
                ],
                "capital.audit_events": [
                    ("a1", {"event_id": "a1"}, None),
                ],
                "capital.allocation_authority": [
                    ("capital-allocation-authority", {
                        "schema_version": 3,
                        "rebalances": {"rb-1": {"rebalance_id": "rb-1"}},
                        "allocations": {"al-1": {"allocation_id": "al-1"}},
                        "containments": {"ct-1": {"containment_id": "ct-1"}},
                        "command_receipts": {"cr-1": {"command_id": "cr-1"}},
                        "containment_commands": {"cc-1": {"command_id": "cc-1"}},
                    }, None),
                ],
            }

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, params=()):
            self.executed_statements.append((sql, params))
            norm = " ".join(sql.replace('"', '').split()).upper()
            if "SELECT PAYLOAD FROM" in norm:
                row = self.tables["capital.allocation_authority"][0]
                return FakeCursor([(json.dumps(row[1]),)])
            if "SELECT RECORD_ID, PAYLOAD, TENANT_ID" in norm:
                for tbl_name, tbl_rows in self.tables.items():
                    if tbl_name.upper() in norm:
                        return FakeCursor([(r[0], json.dumps(r[1]), r[2]) for r in tbl_rows])
            if "INSERT INTO" in norm and "CAPITAL.ALLOCATION_AUTHORITY" in norm:
                self.tables["capital.allocation_authority"][0] = (
                    params[0],
                    json.loads(params[1]) if isinstance(params[1], str) else params[1],
                    params[2],
                )
                return FakeCursor([])
            return FakeCursor([])

    fake_conn = FakeConn()
    fake_psycopg = type("FakePsycopg", (), {"connect": lambda dsn: fake_conn})
    monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg)

    # Test migrate_capital_tables with fake postgres connection
    migrate_capital_tables("postgresql://user:pass@localhost:5432/db", default_tenant="tenant-migrated")

    # Verify DDL executed and index identifiers have no embedded quotes
    ddl_sqls = [sql for sql, _ in fake_conn.executed_statements if "CREATE INDEX" in sql]
    assert len(ddl_sqls) >= 4
    for sql in ddl_sqls:
        assert '"idx_' in sql
        assert '""' not in sql  # no double double-quotes

    # Verify allocation authority row was stamped with tenant-migrated
    row = fake_conn.tables["capital.allocation_authority"][0]
    record_id, payload, sql_tenant_id = row
    assert sql_tenant_id == "tenant-migrated"
    assert payload["tenant_id"] == "tenant-migrated"
    assert payload["rebalances"]["rb-1"]["tenant_id"] == "tenant-migrated"
    assert payload["allocations"]["al-1"]["tenant_id"] == "tenant-migrated"
    assert payload["containments"]["ct-1"]["tenant_id"] == "tenant-migrated"
    assert payload["command_receipts"]["cr-1"]["tenant_id"] == "tenant-migrated"
    assert payload["containment_commands"]["cc-1"]["tenant_id"] == "tenant-migrated"

    # Verify PostgresAllocationAuthorityStore._persist_locked stamps SQL tenant column
    alloc_store = PostgresAllocationAuthorityStore(
        dsn="postgresql://user:pass@localhost:5432/db",
        table="capital.allocation_authority",
        bootstrap=False,
    )
    alloc_store.backfill_tenant(default_tenant="tenant-restored")
    row = fake_conn.tables["capital.allocation_authority"][0]
    assert row[2] == "tenant-migrated"


def test_apply_rebalance_rejects_foreign_and_untenanted_allocations(capital_test_env):
    client, module, tempdir = capital_test_env
    headers_a = _auth_headers("tenant-alpha", actor_id="admin-alpha")

    # 1. Create tenant-alpha pool
    pool_res = client.post(
        "/api/capital-pools",
        json={
            "actor_id": "admin-alpha",
            "actor_role": "capital.admin",
            "pool_id": "pool-rebal-guard",
            "name": "Guard Pool",
            "owner_id": "fund",
            "owner_type": "fund",
        },
        headers=headers_a,
    )
    assert pool_res.status_code == 201

    # 2. Seed matching sleeveless allocation baseline weight 0.5 with tenant_id null
    alloc_id = "pool-rebal-guard|persona:persona-g1"
    store = module.allocation_authority_store
    with store._lock:
        store._reload_locked()
        store._data["allocations"][alloc_id] = {
            "allocation_id": alloc_id,
            "tenant_id": None,
            "capital_pool_id": "pool-rebal-guard",
            "capital_scope": "pool",
            "capital_sleeve_id": None,
            "persona_id": "persona-g1",
            "binding_id": None,
            "binding_state": "bound",
            "stage": "paper",
            "current_weight": 0.5,
            "target_weight": 0.5,
            "allocation_version": 1,
            "updated_at": "2026-09-30T00:00:00Z",
        }
        store._persist_locked()

    # 3. Submit risk-decreasing sleeveless paper rebalance to 0 as tenant-alpha
    line = {
        "ranking_snapshot_id": "snap-g1",
        "allocation_evaluation_id": "eval-g1",
        "allocation_policy_version": "v1",
        "persona_id": "persona-g1",
        "capital_sleeve_id": None,
        "current_weight": 0.5,
        "target_weight": 0.0,
        "capital_scope": "pool",
        "stage": "paper",
    }
    line["allocation_line_digest"] = allocation_line_digest(line)
    prop_res = client.post(
        "/api/rebalances",
        json={
            "actor_id": "admin-alpha",
            "actor_role": "operator",
            "rebalance_id": "rb-guard-null",
            "capital_pool_id": "pool-rebal-guard",
            "ranking_snapshot_id": "snap-g1",
            "allocation_evaluation_id": "eval-g1",
            "allocation_policy_version": "v1",
            "request_hash": "hash-guard-null",
            "idempotency_key": "idem-guard-null",
            "lines": [line],
        },
        headers=headers_a,
    )
    assert prop_res.status_code == 201

    # 4. Attempt to apply rebalance: MUST be rejected, and null row must be UNCHANGED
    apply_res = client.post(
        "/api/rebalances/rb-guard-null/apply",
        json={
            "actor_id": "admin-alpha",
            "actor_role": "operator",
            "command_id": "cmd-guard-null",
            "rebalance_id": "rb-guard-null",
            "request_hash": "hash-guard-null-apply",
            "idempotency_key": "idem-guard-null-apply",
        },
        headers=headers_a,
    )
    assert apply_res.status_code == 409
    with store._lock:
        store._reload_locked()
        assert store._data["allocations"][alloc_id]["current_weight"] == 0.5
        assert store._data["allocations"][alloc_id]["tenant_id"] is None

    # Regression assertions after rejected apply for null row:
    get_res_null = client.get("/api/rebalances/rb-guard-null", headers=headers_a)
    assert get_res_null.status_code == 200
    null_fail = get_res_null.json()["failure"]
    assert null_fail["code"] == "STALE_CURRENT_WEIGHT"
    assert null_fail["details"][0]["reason"] == "allocation_identity_mismatch"
    assert not any("actual_identity" in item for item in null_fail["details"])
    assert "actual_identity" not in get_res_null.text

    list_res_null = client.get("/api/rebalances", headers=headers_a)
    assert list_res_null.status_code == 200
    rb_null_entry = next(r for r in list_res_null.json() if r["rebalance_id"] == "rb-guard-null")
    assert not any("actual_identity" in item for item in rb_null_entry["failure"]["details"])

    replay_null = client.post(
        "/api/rebalances/rb-guard-null/apply",
        json={
            "actor_id": "admin-alpha",
            "actor_role": "operator",
            "command_id": "cmd-guard-null",
            "rebalance_id": "rb-guard-null",
            "request_hash": "hash-guard-null-apply",
            "idempotency_key": "idem-guard-null-apply",
        },
        headers=headers_a,
    )
    assert replay_null.status_code == 409
    assert client.get("/api/rebalances/receipts/cmd-guard-null", headers=headers_a).status_code == 404

    # 5. Now seed with tenant_id: "tenant-beta"
    with store._lock:
        store._data["allocations"][alloc_id]["tenant_id"] = "tenant-beta"
        store._data["allocations"][alloc_id]["current_weight"] = 0.5
        store._persist_locked()

    prop_res2 = client.post(
        "/api/rebalances",
        json={
            "actor_id": "admin-alpha",
            "actor_role": "operator",
            "rebalance_id": "rb-guard-beta",
            "capital_pool_id": "pool-rebal-guard",
            "ranking_snapshot_id": "snap-g1",
            "allocation_evaluation_id": "eval-g1",
            "allocation_policy_version": "v1",
            "request_hash": "hash-guard-beta",
            "idempotency_key": "idem-guard-beta",
            "lines": [line],
        },
        headers=headers_a,
    )
    assert prop_res2.status_code == 201

    # Attempt to apply: MUST be rejected, and foreign row must be UNCHANGED
    apply_res2 = client.post(
        "/api/rebalances/rb-guard-beta/apply",
        json={
            "actor_id": "admin-alpha",
            "actor_role": "operator",
            "command_id": "cmd-guard-beta",
            "rebalance_id": "rb-guard-beta",
            "request_hash": "hash-guard-beta-apply",
            "idempotency_key": "idem-guard-beta-apply",
        },
        headers=headers_a,
    )
    assert apply_res2.status_code == 409
    with store._lock:
        store._reload_locked()
        assert store._data["allocations"][alloc_id]["current_weight"] == 0.5
        assert store._data["allocations"][alloc_id]["tenant_id"] == "tenant-beta"

    # Regression assertions after rejected apply for foreign row:
    get_res_beta = client.get("/api/rebalances/rb-guard-beta", headers=headers_a)
    assert get_res_beta.status_code == 200
    beta_fail = get_res_beta.json()["failure"]
    assert beta_fail["code"] == "STALE_CURRENT_WEIGHT"
    assert beta_fail["details"][0]["reason"] == "allocation_identity_mismatch"
    assert not any("actual_identity" in item for item in beta_fail["details"])
    assert "tenant-beta" not in get_res_beta.text

    list_res_beta = client.get("/api/rebalances", headers=headers_a)
    assert list_res_beta.status_code == 200
    rb_beta_entry = next(r for r in list_res_beta.json() if r["rebalance_id"] == "rb-guard-beta")
    assert not any("actual_identity" in item for item in rb_beta_entry["failure"]["details"])
    assert "tenant-beta" not in json.dumps(rb_beta_entry)

    replay_beta = client.post(
        "/api/rebalances/rb-guard-beta/apply",
        json={
            "actor_id": "admin-alpha",
            "actor_role": "operator",
            "command_id": "cmd-guard-beta",
            "rebalance_id": "rb-guard-beta",
            "request_hash": "hash-guard-beta-apply",
            "idempotency_key": "idem-guard-beta-apply",
        },
        headers=headers_a,
    )
    assert replay_beta.status_code == 409
    assert client.get("/api/rebalances/receipts/cmd-guard-beta", headers=headers_a).status_code == 404

    # Cross-tenant access: tenant-beta cannot view tenant-alpha's rebalance proposal
    headers_b = _auth_headers("tenant-beta", actor_id="admin-beta")
    assert client.get("/api/rebalances/rb-guard-beta", headers=headers_b).status_code == 404

    # 6. When tenant matches ("tenant-alpha"), apply succeeds and weight is updated
    with store._lock:
        store._data["allocations"][alloc_id]["tenant_id"] = "tenant-alpha"
        store._data["allocations"][alloc_id]["current_weight"] = 0.5
        store._persist_locked()

    prop_res3 = client.post(
        "/api/rebalances",
        json={
            "actor_id": "admin-alpha",
            "actor_role": "operator",
            "rebalance_id": "rb-guard-alpha",
            "capital_pool_id": "pool-rebal-guard",
            "ranking_snapshot_id": "snap-g1",
            "allocation_evaluation_id": "eval-g1",
            "allocation_policy_version": "v1",
            "request_hash": "hash-guard-alpha",
            "idempotency_key": "idem-guard-alpha",
            "lines": [line],
        },
        headers=headers_a,
    )
    assert prop_res3.status_code == 201
    apply_res3 = client.post(
        "/api/rebalances/rb-guard-alpha/apply",
        json={
            "actor_id": "admin-alpha",
            "actor_role": "operator",
            "command_id": "cmd-guard-alpha",
            "rebalance_id": "rb-guard-alpha",
            "request_hash": "hash-guard-alpha-apply",
            "idempotency_key": "idem-guard-alpha-apply",
        },
        headers=headers_a,
    )
    assert apply_res3.status_code == 200
    assert apply_res3.json()["allocation_readback"][0]["tenant_id"] == "tenant-alpha"
    assert apply_res3.json()["allocation_readback"][0]["current_weight"] == 0.0
    with store._lock:
        store._reload_locked()
        assert store._data["allocations"][alloc_id]["current_weight"] == 0.0
        assert store._data["allocations"][alloc_id]["tenant_id"] == "tenant-alpha"


def test_real_postgres_migration_regression_preexisting_rows():
    dsn = os.environ.get("CAPITAL_TEST_DSN", "postgresql://postgres:postgres@127.0.0.1:15432/pantheon")
    try:
        import psycopg
        with psycopg.connect(dsn, connect_timeout=2) as conn:
            pass
    except Exception:
        pytest.skip("Disposable PostgreSQL instance not available at " + dsn)

    from services.capital.pg_store import migrate_capital_tables

    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS capital;")
        for tbl in ("capital_pools", "persona_capital_bindings", "allocation_authority", "audit_events"):
            conn.execute(f"DROP TABLE IF EXISTS capital.{tbl} CASCADE;")
            conn.execute(f"CREATE TABLE capital.{tbl} (record_id TEXT PRIMARY KEY, payload JSONB, updated_at TIMESTAMPTZ);")

        conn.execute("INSERT INTO capital.capital_pools (record_id, payload) VALUES ('pool-1', '{\"name\": \"Alpha Pool\", \"metadata\": {}}');")
        conn.execute("INSERT INTO capital.persona_capital_bindings (record_id, payload) VALUES ('bind-1', '{\"binding_id\": \"bind-1\", \"metadata\": {}}');")
        conn.execute("INSERT INTO capital.audit_events (record_id, payload) VALUES ('evt-1', '{\"event_id\": \"evt-1\"}');")
        alloc_doc = {
            "schema_version": 3,
            "allocations": {"al-1": {"allocation_id": "al-1", "current_weight": 0.5}},
            "rebalances": {"rb-1": {"rebalance_id": "rb-1"}},
            "containments": {"ct-1": {"containment_id": "ct-1"}},
            "command_receipts": {"cr-1": {"command_id": "cr-1"}},
            "containment_commands": {"cc-1": {"command_id": "cc-1"}},
        }
        conn.execute("INSERT INTO capital.allocation_authority (record_id, payload) VALUES ('capital-allocation-authority', %s);", (json.dumps(alloc_doc),))
        conn.execute("ALTER DATABASE pantheon SET lock_timeout = '1500ms';")

    try:
        migrate_capital_tables(dsn, default_tenant="tenant-backfilled")

        with psycopg.connect(dsn) as conn:
            for tbl in ("capital.capital_pools", "capital.persona_capital_bindings", "capital.allocation_authority", "capital.audit_events"):
                row = conn.execute(f"SELECT record_id, payload, tenant_id FROM {tbl};").fetchone()
                assert row is not None
                assert row[2] == "tenant-backfilled"

            cur = conn.execute("SELECT payload FROM capital.allocation_authority WHERE record_id = 'capital-allocation-authority';")
            payload = cur.fetchone()[0]
            if isinstance(payload, str):
                payload = json.loads(payload)
            assert payload["tenant_id"] == "tenant-backfilled"
            assert payload["allocations"]["al-1"]["tenant_id"] == "tenant-backfilled"
            assert payload["rebalances"]["rb-1"]["tenant_id"] == "tenant-backfilled"
            assert payload["containments"]["ct-1"]["tenant_id"] == "tenant-backfilled"
            assert payload["command_receipts"]["cr-1"]["tenant_id"] == "tenant-backfilled"
            assert payload["containment_commands"]["cc-1"]["tenant_id"] == "tenant-backfilled"
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute("ALTER DATABASE pantheon RESET lock_timeout;")

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
    # Initially invisble
    with pytest.raises(module.AllocationAuthorityNotFound):
        alloc_store.get_rebalance("rb-old", tenant_id="tenant-restored")

    # Run backfill
    alloc_store.backfill_tenant(default_tenant="tenant-restored")

    # Now visible to restored tenant, but invisible to other tenant
    rb = alloc_store.get_rebalance("rb-old", tenant_id="tenant-restored")
    assert rb["tenant_id"] == "tenant-restored"
    with pytest.raises(module.AllocationAuthorityNotFound):
        alloc_store.get_rebalance("rb-old", tenant_id="tenant-other")

    # Test migrate_capital_tables with dummy DSN doesn't crash if psycopg not installed
    from services.capital.pg_store import migrate_capital_tables
    migrate_capital_tables("postgresql://dummy:5432/db", default_tenant="tenant-restored")

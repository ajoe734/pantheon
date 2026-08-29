"""
Unit tests for the deployable capital service boundary.
"""
from __future__ import annotations

import json
import importlib
import os
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from services.capital.allocation_store import allocation_line_digest


@pytest.fixture()
def client():
    tempdir = tempfile.mkdtemp(prefix="capital_service_")
    env_backup = {
        "CAPITAL_DATA_DIR": os.environ.get("CAPITAL_DATA_DIR"),
        "PANTHEON_GOVERNANCE_DATA_DIR": os.environ.get("PANTHEON_GOVERNANCE_DATA_DIR"),
        "CAPITAL_STORE_BACKEND": os.environ.get("CAPITAL_STORE_BACKEND"),
        "CAPITAL_AUDIT_BACKEND": os.environ.get("CAPITAL_AUDIT_BACKEND"),
        "CAPITAL_AUTH_DISABLED": os.environ.get("CAPITAL_AUTH_DISABLED"),
    }
    os.environ["CAPITAL_DATA_DIR"] = tempdir
    os.environ["PANTHEON_GOVERNANCE_DATA_DIR"] = tempdir
    os.environ["CAPITAL_STORE_BACKEND"] = "json"
    os.environ["CAPITAL_AUDIT_BACKEND"] = "jsonl"
    os.environ["CAPITAL_AUTH_DISABLED"] = "true"

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
        "capital_sleeve_id": "sleeve-alpha",
        "role": "live_owner",
        "allowed_deployment_scope": "live",
    }
    payload.update(overrides)
    return payload


def _rebalance_payload(*, rebalance_id="rb-001", lines=None, **overrides):
    payload = {
        "actor_id": "operator-1",
        "actor_role": "operator",
        "idempotency_key": f"create-{rebalance_id}",
        "request_hash": f"request-create-{rebalance_id}",
        "rebalance_id": rebalance_id,
        "capital_pool_id": "pool-001",
        "ranking_snapshot_id": "ranking-q3",
        "allocation_evaluation_id": "allocation-evaluation-q3",
        "allocation_policy_version": "persona-real-allocation-v1",
        "reason": "quarterly",
        "lines": lines
        or [
            {
                "ranking_snapshot_id": "ranking-q3",
                "allocation_evaluation_id": "allocation-evaluation-q3",
                "allocation_line_digest": "allocation-line-q3-persona-alpha",
                "allocation_policy_version": "persona-real-allocation-v1",
                "persona_id": "persona-alpha",
                "stage": "live_running",
                "capital_scope": "pool",
                "capital_pool_id": "pool-001",
                "capital_sleeve_id": "sleeve-alpha",
                "current_weight": 0.0,
                "target_weight": 0.12,
                "delta": 0.12,
                "cap_reasons": ["quarterly_increase_cap_25pct"],
                "evidence_refs": ["evidence:ranking-q3"],
            }
        ],
        "simulation": {"status": "passed"},
        "constraints": {"pool_total_max": 1.0},
        "rollback_target": {"snapshot_id": "allocation-before-q3"},
        "audit_refs": ["audit:ranking-q3"],
    }
    payload.update(overrides)
    payload["lines"] = [dict(line) for line in payload["lines"]]
    for index, line in enumerate(payload["lines"]):
        line.setdefault("ranking_snapshot_id", payload["ranking_snapshot_id"])
        line.setdefault(
            "allocation_evaluation_id",
            payload["allocation_evaluation_id"],
        )
        line.setdefault(
            "allocation_line_digest",
            f"allocation-line-{rebalance_id}-{index}",
        )
        line.setdefault(
            "allocation_policy_version",
            payload["allocation_policy_version"],
        )
        line["allocation_line_digest"] = allocation_line_digest(line)
    return payload


def _apply_payload(*, rebalance_id="rb-001", command_id="cmd-apply-001", **overrides):
    payload = {
        "actor_id": "approver-1",
        "actor_role": "approver",
        "idempotency_key": f"apply-{rebalance_id}",
        "request_hash": f"request-apply-{rebalance_id}",
        "command_id": command_id,
        "approval_ref": f"approval-{rebalance_id}",
    }
    payload.update(overrides)
    return payload


def _reload_capital_module():
    module = importlib.import_module("services.capital.main")
    return importlib.reload(module)


def _create_default_pool_and_binding(test_client):
    assert test_client.post("/api/capital-pools", json=_pool_payload()).status_code == 201
    response = test_client.post("/api/bindings", json=_binding_payload())
    assert response.status_code == 201, response.text


def test_health(client):
    test_client, _ = client
    response = test_client.get("/health")
    assert response.status_code == 200
    assert response.json()["service"] == "pantheon-capital"


def test_capital_mutations_bind_verified_actor_role_and_tenant():
    from services.runtime_auth_inbound import encode_jwt_hs256

    tempdir = tempfile.mkdtemp(prefix="capital_auth_")
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
    backup = {key: os.environ.get(key) for key in keys}
    try:
        os.environ.update(
            {
                "CAPITAL_DATA_DIR": tempdir,
                "PANTHEON_GOVERNANCE_DATA_DIR": tempdir,
                "CAPITAL_STORE_BACKEND": "json",
                "CAPITAL_AUDIT_BACKEND": "jsonl",
                "CAPITAL_AUTH_DISABLED": "false",
                "CAPITAL_AUTH_MODE": "strict",
                "CAPITAL_JWT_SECRET": "capital-test-secret",
                "CAPITAL_ALLOWED_CALLER_SERVICES": "control-plane-bff",
            }
        )
        sys.modules.pop("services.capital.main", None)
        module = importlib.import_module("services.capital.main")
        module = importlib.reload(module)
        test_client = TestClient(module.app)
        token = encode_jwt_hs256(
            {
                "sub": "control-plane-bff",
                "service": "control-plane-bff",
                "roles": ["capital.admin"],
                "allowed_tenants": ["tenant-capital-a"],
                "delegated_actor_id": "capital-admin-1",
                "exp": int(time.time()) + 300,
            },
            secret="capital-test-secret",
        )
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Tenant-Id": "tenant-capital-a",
            "X-Pantheon-Service": "control-plane-bff",
        }

        created = test_client.post(
            "/api/capital-pools",
            json=_pool_payload(),
            headers=headers,
        )
        assert created.status_code == 201, created.text
        assert created.headers["X-Pantheon-Tenant"] == "tenant-capital-a"

        missing_auth = test_client.post(
            "/api/capital-pools",
            json=_pool_payload(pool_id="pool-no-auth"),
        )
        assert missing_auth.status_code == 400
        assert missing_auth.json()["error"]["code"] == "TENANT_REQUIRED"

        wrong_tenant = test_client.post(
            "/api/capital-pools",
            json=_pool_payload(pool_id="pool-wrong-tenant"),
            headers={**headers, "X-Tenant-Id": "tenant-capital-b"},
        )
        assert wrong_tenant.status_code == 403
        assert wrong_tenant.json()["error"]["code"] == "TENANT_SCOPE_FORBIDDEN"

        spoofed_actor = test_client.post(
            "/api/capital-pools",
            json=_pool_payload(
                pool_id="pool-spoofed-actor",
                actor_id="attacker",
            ),
            headers=headers,
        )
        assert spoofed_actor.status_code == 403
        assert "authenticated actor" in spoofed_actor.json()["detail"]

        spoofed_role = test_client.post(
            "/api/capital-pools",
            json=_pool_payload(
                pool_id="pool-spoofed-role",
                actor_role="persona.admin",
            ),
            headers=headers,
        )
        assert spoofed_role.status_code == 403
        assert "verified token" in spoofed_role.json()["detail"]
    finally:
        for key, value in backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_write_authority_matrix(client):
    test_client, _ = client
    response = test_client.get("/api/capital/write-authority")
    assert response.status_code == 200
    matrix = response.json()["matrix"]
    expected = {
        ("CapitalPool", "create"),
        ("CapitalPool", "update"),
        ("CapitalPool", "update_status"),
        ("PersonaCapitalBinding", "create"),
        ("PersonaCapitalBinding", "activate"),
        ("PersonaCapitalBinding", "update_status"),
        ("Rebalance", "create"),
        ("Rebalance", "apply"),
        ("Containment", "create"),
    }
    found = {(entry["resource_type"], entry["operation"]) for entry in matrix}
    assert expected == found
    roles_by_operation = {
        (entry["resource_type"], entry["operation"]): set(entry["authorized_roles"])
        for entry in matrix
    }
    assert "reviewer" in roles_by_operation[("Containment", "create")]
    assert "reviewer" not in roles_by_operation[("Rebalance", "create")]
    assert "reviewer" not in roles_by_operation[("Rebalance", "apply")]


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


def test_patch_pool_survives_fresh_service_instance(client):
    test_client, _ = client
    assert test_client.post("/api/capital-pools", json=_pool_payload()).status_code == 201

    patched = test_client.patch(
        "/api/capital-pools/pool-001",
        json={
            "actor_id": "capital-admin-1",
            "actor_role": "capital.admin",
            "name": "Renamed Owner Pool",
            "risk_policy_ref": "risk-v2",
            "params": {"paper_limit": 100000},
        },
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["name"] == "Renamed Owner Pool"

    fresh_client = TestClient(_reload_capital_module().app)
    readback = fresh_client.get("/api/capital-pools/pool-001")
    assert readback.status_code == 200
    assert readback.json()["risk_policy_ref"] == "risk-v2"
    assert readback.json()["metadata"]["params"] == {"paper_limit": 100000}


def test_pool_and_binding_create_idempotency_survive_restart(client):
    test_client, _ = client
    pool_payload = _pool_payload(
        actor_id="operator-1",
        actor_role="operator",
        idempotency_key="pool-create-1",
        request_hash="pool-request-1",
    )
    created_pool = test_client.post("/api/capital-pools", json=pool_payload)
    assert created_pool.status_code == 201, created_pool.text
    assert created_pool.json()["idempotent_replay"] is False
    binding_payload = _binding_payload(
        actor_id="approver-1",
        actor_role="approver",
        idempotency_key="binding-create-1",
        request_hash="binding-request-1",
    )
    created_binding = test_client.post("/api/bindings", json=binding_payload)
    assert created_binding.status_code == 201, created_binding.text
    assert created_binding.json()["capital_sleeve_id"] == "sleeve-alpha"

    restarted = TestClient(_reload_capital_module().app)
    pool_replay = restarted.post("/api/capital-pools", json=pool_payload)
    binding_replay = restarted.post("/api/bindings", json=binding_payload)
    assert pool_replay.status_code == 201, pool_replay.text
    assert binding_replay.status_code == 201, binding_replay.text
    assert pool_replay.json()["idempotent_replay"] is True
    assert binding_replay.json()["idempotent_replay"] is True
    assert binding_replay.json()["capital_sleeve_id"] == "sleeve-alpha"

    conflict = restarted.post(
        "/api/capital-pools",
        json={**pool_payload, "name": "Different semantic body"},
    )
    assert conflict.status_code == 409


def test_pool_create_idempotency_is_actor_scoped(client):
    test_client, _ = client
    shared = _pool_payload(
        pool_id=None,
        actor_id="operator-a",
        actor_role="operator",
        idempotency_key="shared-owner-create-key",
        request_hash="shared-owner-create-request",
    )
    first = test_client.post("/api/capital-pools", json=shared)
    second = test_client.post(
        "/api/capital-pools",
        json={**shared, "actor_id": "operator-b"},
    )
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["pool_id"] != second.json()["pool_id"]
    assert first.json()["idempotent_replay"] is False
    assert second.json()["idempotent_replay"] is False


def test_owner_create_legacy_idempotency_entry_replays_and_migrates_schema(tmp_path):
    from services.capital.allocation_store import AllocationAuthorityStore

    path = tmp_path / "allocation-authority.json"
    legacy_entry = {
        "operation": "capital_pool.create",
        "request_hash": "legacy-request",
        "payload_hash": "legacy-payload",
        "resource_id": "pool-legacy",
        "status": "succeeded",
        "created_at": "2026-07-13T00:00:00Z",
        "updated_at": "2026-07-13T00:00:00Z",
    }
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "owner_create_idempotency": {
                    "capital_pool.create:legacy-key": legacy_entry,
                },
            }
        ),
        encoding="utf-8",
    )
    store = AllocationAuthorityStore(path=path)
    replay, replayed = store.reserve_owner_create(
        scope="capital_pool.create",
        actor_scope="operator-legacy",
        key="legacy-key",
        request_hash="legacy-request",
        payload_hash="legacy-payload",
        resource_id="pool-legacy",
    )
    assert replayed is True
    assert replay["resource_id"] == "pool-legacy"
    store.complete_owner_create(
        scope="capital_pool.create",
        actor_scope="operator-legacy",
        key="legacy-key",
    )
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 3


def test_postgres_pool_and_binding_refreshes_are_serialized():
    from services.capital import pg_store

    class ConcurrentProbeRecords:
        def __init__(self):
            self.active = 0
            self.peak = 0
            self.lock = threading.Lock()

        def list_all(self):
            with self.lock:
                self.active += 1
                self.peak = max(self.peak, self.active)
            time.sleep(0.05)
            with self.lock:
                self.active -= 1
            return []

    cases = (
        (
            pg_store.PostgresCapitalPoolStore,
            pg_store.CapitalPoolStore,
            "pool-missing",
        ),
        (
            pg_store.PostgresPersonaCapitalBindingStore,
            pg_store.PersonaCapitalBindingStore,
            "binding-missing",
        ),
    )
    for postgres_type, base_type, missing_id in cases:
        store = postgres_type.__new__(postgres_type)
        base_type.__init__(store, path=None)
        records = ConcurrentProbeRecords()
        store._records = records
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(store.get, missing_id) for _ in range(2)]
            assert [future.result(timeout=2) for future in futures] == [None, None]
        assert records.peak == 1


def test_concurrent_pool_create_replay_returns_one_resource(client, monkeypatch):
    test_client, _ = client
    module = importlib.import_module("services.capital.main")
    original_create = module.pool_store.create
    first_create_entered = threading.Event()
    release_first_create = threading.Event()
    call_count = 0
    count_lock = threading.Lock()

    def delayed_create(pool):
        nonlocal call_count
        with count_lock:
            call_count += 1
            ordinal = call_count
        if ordinal == 1:
            first_create_entered.set()
            assert release_first_create.wait(timeout=5)
        return original_create(pool)

    monkeypatch.setattr(module.pool_store, "create", delayed_create)
    payload = _pool_payload(
        actor_id="operator-concurrent",
        actor_role="operator",
        idempotency_key="pool-create-concurrent",
        request_hash="pool-request-concurrent",
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(
            test_client.post,
            "/api/capital-pools",
            json=payload,
        )
        assert first_create_entered.wait(timeout=5)
        second_future = executor.submit(
            test_client.post,
            "/api/capital-pools",
            json=payload,
        )
        time.sleep(0.1)
        release_first_create.set()
        first = first_future.result(timeout=5)
        second = second_future.result(timeout=5)

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["pool_id"] == second.json()["pool_id"]
    assert {first.json()["idempotent_replay"], second.json()["idempotent_replay"]} == {
        False,
        True,
    }
    assert call_count == 1


def test_pool_sleeve_binding_identity_is_unique(client):
    test_client, _ = client
    assert test_client.post("/api/capital-pools", json=_pool_payload()).status_code == 201
    assert test_client.post("/api/bindings", json=_binding_payload()).status_code == 201
    duplicate = test_client.post(
        "/api/bindings",
        json=_binding_payload(
            binding_id="binding-duplicate-sleeve",
            persona_id="persona-beta",
        ),
    )
    assert duplicate.status_code == 400
    assert "already bound" in duplicate.json()["detail"]


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

    create = test_client.post(
        "/api/bindings",
        json=_binding_payload(allowed_deployment_scope="canary"),
    )
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
        json=_binding_payload(
            binding_id="binding-002",
            persona_id="persona-beta",
            capital_sleeve_id="sleeve-beta",
        ),
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


def test_rebalance_apply_updates_authoritative_allocations_and_replays_once(client):
    test_client, _ = client
    _create_default_pool_and_binding(test_client)

    proposal_payload = _rebalance_payload()
    created = test_client.post("/api/rebalances", json=proposal_payload)
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "pending"
    assert created.json()["applied"] is False
    assert test_client.get("/api/allocations").json()["count"] == 0

    denied_payload = _apply_payload(approval_ref=None)
    denied = test_client.post("/api/rebalances/rb-001/apply", json=denied_payload)
    assert denied.status_code == 409
    assert "approval reference" in denied.json()["detail"]

    apply_payload = _apply_payload()
    applied = test_client.post("/api/rebalances/rb-001/apply", json=apply_payload)
    assert applied.status_code == 200, applied.text
    receipt = applied.json()
    assert receipt["status"] == "applied"
    assert receipt["rebalance_id"] == "rb-001"
    assert receipt["command_id"] == "cmd-apply-001"
    assert receipt["approval_ref"] == "approval-rb-001"
    assert receipt["receipt_ref"]
    assert receipt["audit_ref"]
    assert receipt["authoritative_capital_readback"] is True
    assert receipt["authoritative_capital_state_applied"] is True
    assert receipt["live_capital_side_effects"] is False
    assert receipt["allocation_readback"][0]["current_weight"] == 0.12
    assert receipt["allocation_readback"][0]["target_weight"] == 0.12
    assert receipt["allocation_readback"][0]["allocation_version"] == 1
    assert receipt["allocation_readback"][0]["authoritative_capital_readback"] is True
    receipt_lookup = test_client.get(
        "/api/rebalances/receipts/cmd-apply-001"
    )
    assert receipt_lookup.status_code == 200, receipt_lookup.text
    assert receipt_lookup.json()["receipt_ref"] == receipt["receipt_ref"]
    assert receipt_lookup.json()["audit_delivery_status"] == "delivered"

    detail = test_client.get("/api/rebalances/rb-001")
    assert detail.status_code == 200
    assert detail.json()["status"] == "applied"
    assert detail.json()["applied"] is True
    assert detail.json()["approval_ref"] == "approval-rb-001"
    assert detail.json()["apply_command_id"] == "cmd-apply-001"
    assert detail.json()["apply_receipt_ref"] == receipt["receipt_ref"]

    global_read = test_client.get(
        "/api/allocations",
        params={"capital_pool_id": "pool-001", "persona_id": "persona-alpha"},
    )
    assert global_read.status_code == 200
    assert global_read.json()["authoritative_capital_readback"] is True
    assert global_read.json()["source"] == "capital_service"
    assert global_read.json()["count"] == 1
    allocation = global_read.json()["items"][0]
    assert allocation["authoritative_capital_readback"] is True
    assert allocation["capital_sleeve_id"] == "sleeve-alpha"
    assert allocation["current_weight"] == 0.12
    assert allocation["last_rebalance_id"] == "rb-001"

    pool_read = test_client.get("/api/capital-pools/pool-001/allocations")
    assert pool_read.status_code == 200
    assert pool_read.json()["items"] == global_read.json()["items"]

    replay = test_client.post("/api/rebalances/rb-001/apply", json=apply_payload)
    assert replay.status_code == 200, replay.text
    assert replay.json()["receipt_ref"] == receipt["receipt_ref"]
    assert replay.json()["idempotent_replay"] is True
    allocation_after_replay = test_client.get("/api/allocations").json()["items"][0]
    assert allocation_after_replay["allocation_version"] == 1

    create_replay = test_client.post("/api/rebalances", json=proposal_payload)
    assert create_replay.status_code == 201
    assert create_replay.json()["rebalance_id"] == "rb-001"
    conflicting_payload = {**proposal_payload, "reason": "different body"}
    conflict = test_client.post("/api/rebalances", json=conflicting_payload)
    assert conflict.status_code == 409


@pytest.mark.parametrize(
    "field",
    (
        "ranking_snapshot_id",
        "allocation_evaluation_id",
        "allocation_policy_version",
    ),
)
def test_rebalance_admission_requires_outer_allocation_lineage(client, field):
    test_client, _ = client
    payload = _rebalance_payload(rebalance_id=f"rb-missing-outer-{field}")
    payload.pop(field)

    response = test_client.post("/api/rebalances", json=payload)

    assert response.status_code == 422


@pytest.mark.parametrize(
    "field",
    (
        "ranking_snapshot_id",
        "allocation_evaluation_id",
        "allocation_line_digest",
        "allocation_policy_version",
    ),
)
def test_rebalance_admission_requires_per_line_allocation_lineage(client, field):
    test_client, _ = client
    payload = _rebalance_payload(rebalance_id=f"rb-missing-line-{field}")
    payload["lines"][0].pop(field)

    response = test_client.post("/api/rebalances", json=payload)

    assert response.status_code == 422


@pytest.mark.parametrize(
    "field",
    (
        "ranking_snapshot_id",
        "allocation_evaluation_id",
        "allocation_policy_version",
    ),
)
def test_rebalance_store_rejects_lineage_mismatch_with_outer_evaluation(
    client,
    field,
):
    test_client, data_dir = client
    _create_default_pool_and_binding(test_client)
    payload = _rebalance_payload(rebalance_id=f"rb-mismatch-{field}")
    payload["lines"][0][field] = f"attacker-{field}"

    response = test_client.post("/api/rebalances", json=payload)

    assert response.status_code == 422, response.text
    assert field in response.json()["detail"]
    persisted_path = data_dir / "capital_allocation_authority.json"
    if persisted_path.exists():
        persisted = json.loads(persisted_path.read_text(encoding="utf-8"))
        assert payload["rebalance_id"] not in persisted.get("rebalances", {})


def test_rebalance_store_rejects_blank_line_digest(client):
    test_client, _ = client
    _create_default_pool_and_binding(test_client)
    payload = _rebalance_payload(rebalance_id="rb-blank-line-digest")
    payload["lines"][0]["allocation_line_digest"] = "   "

    response = test_client.post("/api/rebalances", json=payload)

    assert response.status_code == 422, response.text
    assert "allocation_line_digest is required" in response.json()["detail"]


def test_rebalance_store_rejects_line_digest_that_does_not_match_tuple(client):
    test_client, _ = client
    _create_default_pool_and_binding(test_client)
    payload = _rebalance_payload(rebalance_id="rb-forged-line-digest")
    payload["lines"][0]["allocation_line_digest"] = "0" * 64

    response = test_client.post("/api/rebalances", json=payload)

    assert response.status_code == 422, response.text
    assert "does not match the admitted allocation tuple" in response.json()["detail"]


def test_rebalance_allocation_lineage_survives_owner_store_restart(client):
    test_client, _ = client
    _create_default_pool_and_binding(test_client)
    payload = _rebalance_payload(rebalance_id="rb-lineage-restart")

    created = test_client.post("/api/rebalances", json=payload)

    assert created.status_code == 201, created.text
    created_body = created.json()
    for field in (
        "ranking_snapshot_id",
        "allocation_evaluation_id",
        "allocation_policy_version",
    ):
        assert created_body[field] == payload[field]
        assert created_body["lines"][0][field] == payload[field]
    assert (
        created_body["lines"][0]["allocation_line_digest"]
        == payload["lines"][0]["allocation_line_digest"]
    )

    restarted_module = _reload_capital_module()
    restarted = TestClient(restarted_module.app)
    readback = restarted.get("/api/rebalances/rb-lineage-restart")

    assert readback.status_code == 200, readback.text
    assert readback.json() == created_body


def test_rebalance_proposal_receipt_and_allocations_survive_store_restart(client):
    test_client, data_dir = client
    _create_default_pool_and_binding(test_client)
    assert test_client.post("/api/rebalances", json=_rebalance_payload()).status_code == 201
    applied = test_client.post(
        "/api/rebalances/rb-001/apply",
        json=_apply_payload(),
    )
    assert applied.status_code == 200, applied.text
    receipt_ref = applied.json()["receipt_ref"]

    persisted_path = data_dir / "capital_allocation_authority.json"
    assert persisted_path.exists()
    persisted = persisted_path.read_text(encoding="utf-8")
    assert "rb-001" in persisted
    assert receipt_ref in persisted

    restarted_module = _reload_capital_module()
    restarted = TestClient(restarted_module.app)
    proposal = restarted.get("/api/rebalances/rb-001")
    assert proposal.status_code == 200, proposal.text
    assert proposal.json()["status"] == "applied"
    assert proposal.json()["apply_receipt"]["receipt_ref"] == receipt_ref
    allocation = restarted.get("/api/allocations").json()["items"][0]
    assert allocation["current_weight"] == 0.12
    assert allocation["last_rebalance_id"] == "rb-001"

    replay = restarted.post(
        "/api/rebalances/rb-001/apply",
        json=_apply_payload(),
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["receipt_ref"] == receipt_ref
    assert replay.json()["idempotent_replay"] is True
    assert restarted.get("/api/allocations").json()["items"][0]["allocation_version"] == 1


@pytest.mark.parametrize("nonzero_baseline", [1e-13, 0.10])
def test_nonzero_missing_allocation_baseline_fails_terminal(client, nonzero_baseline):
    test_client, _ = client
    assert test_client.post("/api/capital-pools", json=_pool_payload()).status_code == 201
    assert test_client.post(
        "/api/bindings",
        json=_binding_payload(
            persona_id="persona-missing",
            capital_sleeve_id="sleeve-missing",
        ),
    ).status_code == 201
    line = [
        {
            "persona_id": "persona-missing",
            "stage": "live_running",
            "capital_scope": "pool",
            "capital_pool_id": "pool-001",
            "capital_sleeve_id": "sleeve-missing",
            "current_weight": nonzero_baseline,
            "target_weight": 0.20,
            "delta": 0.20 - nonzero_baseline,
        }
    ]
    created = test_client.post(
        "/api/rebalances",
        json=_rebalance_payload(rebalance_id="rb-missing", lines=line),
    )
    assert created.status_code == 201, created.text
    assert test_client.get("/api/allocations").json()["count"] == 0
    applied = test_client.post(
        "/api/rebalances/rb-missing/apply",
        json=_apply_payload(rebalance_id="rb-missing", command_id="cmd-missing"),
    )
    assert applied.status_code == 409
    failed = test_client.get("/api/rebalances/rb-missing").json()
    assert failed["status"] == "failed"
    assert failed["failure"]["details"][0]["reason"] == "allocation_missing"
    assert test_client.get("/api/allocations").json()["count"] == 0


def test_apply_rejects_mismatched_authoritative_allocation_identity(client):
    test_client, data_dir = client
    _create_default_pool_and_binding(test_client)
    assert test_client.post("/api/rebalances", json=_rebalance_payload()).status_code == 201
    assert test_client.post(
        "/api/rebalances/rb-001/apply",
        json=_apply_payload(),
    ).status_code == 200

    follow_up_line = [
        {
            "persona_id": "persona-alpha",
            "stage": "live_running",
            "capital_scope": "pool",
            "capital_pool_id": "pool-001",
            "capital_sleeve_id": "sleeve-alpha",
            "current_weight": 0.12,
            "target_weight": 0.20,
            "delta": 0.08,
        }
    ]
    assert test_client.post(
        "/api/rebalances",
        json=_rebalance_payload(rebalance_id="rb-identity", lines=follow_up_line),
    ).status_code == 201

    store_path = data_dir / "capital_allocation_authority.json"
    aggregate = json.loads(store_path.read_text(encoding="utf-8"))
    allocation = next(iter(aggregate["allocations"].values()))
    allocation["persona_id"] = "persona-tampered"
    store_path.write_text(json.dumps(aggregate), encoding="utf-8")

    rejected = test_client.post(
        "/api/rebalances/rb-identity/apply",
        json=_apply_payload(
            rebalance_id="rb-identity",
            command_id="cmd-identity",
        ),
    )
    assert rejected.status_code == 409, rejected.text
    failure = test_client.get("/api/rebalances/rb-identity").json()["failure"]
    assert failure["details"][0]["reason"] == "allocation_identity_mismatch"


def test_rebalance_binding_must_be_in_effective_window(client):
    test_client, _ = client
    assert test_client.post("/api/capital-pools", json=_pool_payload()).status_code == 201
    binding = test_client.post(
        "/api/bindings",
        json=_binding_payload(effective_to="2020-01-01T00:00:00Z"),
    )
    assert binding.status_code == 201, binding.text
    proposal = test_client.post("/api/rebalances", json=_rebalance_payload())
    assert proposal.status_code == 400
    assert "eligible PersonaCapitalBinding" in proposal.json()["detail"]


def test_risk_increasing_rebalance_requires_capital_sleeve(client):
    test_client, _ = client
    assert test_client.post("/api/capital-pools", json=_pool_payload()).status_code == 201
    line = [
        {
            "persona_id": "persona-alpha",
            "stage": "live_running",
            "capital_scope": "pool",
            "capital_pool_id": "pool-001",
            "current_weight": 0.0,
            "target_weight": 0.10,
            "delta": 0.10,
        }
    ]
    response = test_client.post(
        "/api/rebalances",
        json=_rebalance_payload(rebalance_id="rb-no-sleeve", lines=line),
    )
    assert response.status_code == 400
    assert "requires capital_sleeve_id" in response.json()["detail"]


def test_paper_ledger_increase_uses_unique_sleeveless_paper_binding(client):
    test_client, _ = client
    assert test_client.post(
        "/api/capital-pools",
        json=_pool_payload(
            pool_id="pool-paper",
            owner_type="org",
            metadata={
                "internal": True,
                "execution_context": "paper",
                "persona_id": "persona-paper",
            },
        ),
    ).status_code == 201
    binding = test_client.post(
        "/api/bindings",
        json=_binding_payload(
            binding_id="binding-paper",
            persona_id="persona-paper",
            capital_pool_id="pool-paper",
            capital_sleeve_id=None,
            role="paper_owner",
            allowed_deployment_scope="paper",
        ),
    )
    assert binding.status_code == 201, binding.text
    line = {
        "persona_id": "persona-paper",
        "stage": "paper_running",
        "capital_scope": "paper_ledger",
        "paper_ledger_id": "paper-ledger-persona-paper",
        "capital_pool_id": "pool-paper",
        "capital_sleeve_id": None,
        "current_weight": 0.0,
        "target_weight": 1.0,
        "delta": 1.0,
        "authority_mode": "governed_paper_simulation",
        "promotion_review_id": "review-persona-paper",
        "paper_allocation_eligible": True,
        "live_capital_side_effects": False,
    }
    proposal = test_client.post(
        "/api/rebalances",
        json=_rebalance_payload(
            rebalance_id="rb-paper",
            capital_pool_id="pool-paper",
            allocation_policy_version="persona-paper-allocation-simulation-v1",
            lines=[line],
        ),
    )
    assert proposal.status_code == 201, proposal.text
    persisted_line = proposal.json()["lines"][0]
    assert persisted_line["binding_id"] == "binding-paper"
    assert persisted_line["capital_sleeve_id"] is None
    assert persisted_line["allocation_id"] == "pool-paper|persona:persona-paper"

    applied = test_client.post(
        "/api/rebalances/rb-paper/apply",
        json=_apply_payload(
            rebalance_id="rb-paper",
            command_id="cmd-paper",
            approval_ref="approval-paper",
        ),
    )
    assert applied.status_code == 200, applied.text
    readback = applied.json()["allocation_readback"][0]
    assert readback["capital_scope"] == "paper_ledger"
    assert readback["binding_id"] == "binding-paper"
    assert readback["capital_sleeve_id"] is None
    assert readback["current_weight"] == 1.0
    assert readback["authoritative_capital_readback"] is True


@pytest.mark.parametrize(
    ("role", "allowed_scope"),
    [
        ("live_owner", "canary"),
        ("paper_owner", "paper"),
        ("advisor", "none"),
    ],
)
def test_binding_scope_and_role_ceiling_deny_live_increase(
    client,
    role,
    allowed_scope,
):
    test_client, _ = client
    assert test_client.post("/api/capital-pools", json=_pool_payload()).status_code == 201
    binding = test_client.post(
        "/api/bindings",
        json=_binding_payload(role=role, allowed_deployment_scope=allowed_scope),
    )
    assert binding.status_code == 201, binding.text
    proposal = test_client.post("/api/rebalances", json=_rebalance_payload())
    assert proposal.status_code == 400
    assert "risk-increasing live line" in proposal.json()["detail"]


@pytest.mark.parametrize(
    ("role", "allowed_scope", "stage"),
    [
        ("paper_owner", "paper", "paper"),
        ("paper_owner", "paper", "paper_candidate"),
        ("live_owner", "canary", "canary"),
        ("live_owner", "canary", "canary_candidate"),
        ("live_owner", "live", "live_running"),
        ("live_owner", "live", "live_candidate"),
    ],
)
def test_stage_map_accepts_authorized_risk_increase(
    client,
    role,
    allowed_scope,
    stage,
):
    test_client, _ = client
    assert test_client.post("/api/capital-pools", json=_pool_payload()).status_code == 201
    binding = test_client.post(
        "/api/bindings",
        json=_binding_payload(role=role, allowed_deployment_scope=allowed_scope),
    )
    assert binding.status_code == 201, binding.text
    line = [
        {
            "persona_id": "persona-alpha",
            "stage": stage,
            "capital_scope": "pool",
            "capital_pool_id": "pool-001",
            "capital_sleeve_id": "sleeve-alpha",
            "current_weight": 0.0,
            "target_weight": 0.10,
            "delta": 0.10,
        }
    ]
    proposal = test_client.post(
        "/api/rebalances",
        json=_rebalance_payload(rebalance_id=f"rb-stage-{stage}", lines=line),
    )
    assert proposal.status_code == 201, proposal.text


def test_unknown_stage_fails_closed_for_risk_increase(client):
    test_client, _ = client
    _create_default_pool_and_binding(test_client)
    line = [
        {
            "persona_id": "persona-alpha",
            "stage": "experimental_running",
            "capital_scope": "pool",
            "capital_pool_id": "pool-001",
            "capital_sleeve_id": "sleeve-alpha",
            "current_weight": 0.0,
            "target_weight": 0.10,
            "delta": 0.10,
        }
    ]
    proposal = test_client.post(
        "/api/rebalances",
        json=_rebalance_payload(rebalance_id="rb-unknown-stage", lines=line),
    )
    assert proposal.status_code == 400
    assert "Unsupported risk-increasing rebalance stage" in proposal.json()["detail"]


def test_live_candidate_increase_requires_approval(client):
    test_client, _ = client
    _create_default_pool_and_binding(test_client)
    line = [
        {
            "persona_id": "persona-alpha",
            "stage": "live_candidate",
            "capital_scope": "pool",
            "capital_pool_id": "pool-001",
            "capital_sleeve_id": "sleeve-alpha",
            "current_weight": 0.0,
            "target_weight": 0.10,
            "delta": 0.10,
        }
    ]
    assert test_client.post(
        "/api/rebalances",
        json=_rebalance_payload(rebalance_id="rb-live-candidate", lines=line),
    ).status_code == 201
    denied = test_client.post(
        "/api/rebalances/rb-live-candidate/apply",
        json=_apply_payload(
            rebalance_id="rb-live-candidate",
            command_id="cmd-live-candidate",
            approval_ref=None,
        ),
    )
    assert denied.status_code == 409
    assert "approval reference" in denied.json()["detail"]


@pytest.mark.parametrize("pool_status", ["suspended", "archived"])
def test_inactive_pool_rejects_risk_increase_at_proposal(client, pool_status):
    test_client, _ = client
    _create_default_pool_and_binding(test_client)
    status = test_client.patch(
        "/api/capital-pools/pool-001/status",
        json={
            "actor_id": "capital-admin-1",
            "actor_role": "capital.admin",
            "status": pool_status,
        },
    )
    assert status.status_code == 200, status.text
    proposal = test_client.post("/api/rebalances", json=_rebalance_payload())
    assert proposal.status_code == 400
    assert "must be active" in proposal.json()["detail"]


def test_first_apply_revalidates_pool_and_receipt_replay_survives_suspend(client):
    test_client, _ = client
    _create_default_pool_and_binding(test_client)
    assert test_client.post(
        "/api/rebalances",
        json=_rebalance_payload(rebalance_id="rb-pool-revalidate"),
    ).status_code == 201
    apply_payload = _apply_payload(
        rebalance_id="rb-pool-revalidate",
        command_id="cmd-pool-revalidate",
    )
    assert test_client.patch(
        "/api/capital-pools/pool-001/status",
        json={
            "actor_id": "capital-admin-1",
            "actor_role": "capital.admin",
            "status": "suspended",
        },
    ).status_code == 200
    blocked = test_client.post(
        "/api/rebalances/rb-pool-revalidate/apply",
        json=apply_payload,
    )
    assert blocked.status_code == 409, blocked.text
    assert "must be active" in blocked.json()["detail"]

    assert test_client.patch(
        "/api/capital-pools/pool-001/status",
        json={
            "actor_id": "capital-admin-1",
            "actor_role": "capital.admin",
            "status": "active",
        },
    ).status_code == 200
    applied = test_client.post(
        "/api/rebalances/rb-pool-revalidate/apply",
        json=apply_payload,
    )
    assert applied.status_code == 200, applied.text
    assert test_client.patch(
        "/api/capital-pools/pool-001/status",
        json={
            "actor_id": "capital-admin-1",
            "actor_role": "capital.admin",
            "status": "suspended",
        },
    ).status_code == 200
    replay = test_client.post(
        "/api/rebalances/rb-pool-revalidate/apply",
        json=apply_payload,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["idempotent_replay"] is True


def test_first_apply_revalidates_binding_stage_scope(client):
    test_client, _ = client
    _create_default_pool_and_binding(test_client)
    assert test_client.post(
        "/api/rebalances",
        json=_rebalance_payload(rebalance_id="rb-scope-revalidate"),
    ).status_code == 201
    module = importlib.import_module("services.capital.main")
    with module.binding_store._lock:
        binding = module.binding_store.require("binding-001")
        module.binding_store._bindings["binding-001"] = replace(
            binding,
            allowed_deployment_scope="canary",
        )
    blocked = test_client.post(
        "/api/rebalances/rb-scope-revalidate/apply",
        json=_apply_payload(
            rebalance_id="rb-scope-revalidate",
            command_id="cmd-scope-revalidate",
        ),
    )
    assert blocked.status_code == 409, blocked.text
    assert "no longer eligible" in blocked.json()["detail"]


def test_pool_status_change_cannot_interleave_first_apply(client, monkeypatch):
    test_client, _ = client
    _create_default_pool_and_binding(test_client)
    assert test_client.post(
        "/api/rebalances",
        json=_rebalance_payload(rebalance_id="rb-pool-lock"),
    ).status_code == 201
    module = importlib.import_module("services.capital.main")
    original_apply = module.allocation_authority_store.apply_rebalance
    owner_apply_entered = threading.Event()
    release_owner_apply = threading.Event()

    def delayed_apply(rebalance_id, payload):
        owner_apply_entered.set()
        assert release_owner_apply.wait(timeout=5)
        return original_apply(rebalance_id, payload)

    monkeypatch.setattr(
        module.allocation_authority_store,
        "apply_rebalance",
        delayed_apply,
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        apply_future = executor.submit(
            test_client.post,
            "/api/rebalances/rb-pool-lock/apply",
            json=_apply_payload(
                rebalance_id="rb-pool-lock",
                command_id="cmd-pool-lock",
            ),
        )
        assert owner_apply_entered.wait(timeout=5)
        status_future = executor.submit(
            test_client.patch,
            "/api/capital-pools/pool-001/status",
            json={
                "actor_id": "capital-admin-1",
                "actor_role": "capital.admin",
                "status": "suspended",
            },
        )
        time.sleep(0.1)
        assert status_future.done() is False
        release_owner_apply.set()
        applied = apply_future.result(timeout=5)
        status = status_future.result(timeout=5)
    assert applied.status_code == 200, applied.text
    assert status.status_code == 200, status.text
    replay = test_client.post(
        "/api/rebalances/rb-pool-lock/apply",
        json=_apply_payload(
            rebalance_id="rb-pool-lock",
            command_id="cmd-pool-lock",
        ),
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["idempotent_replay"] is True


def test_first_apply_revalidates_binding_but_successful_replay_survives_revoke(client):
    test_client, _ = client
    _create_default_pool_and_binding(test_client)
    assert test_client.post(
        "/api/rebalances",
        json=_rebalance_payload(rebalance_id="rb-revoked-before"),
    ).status_code == 201
    revoked = test_client.patch(
        "/api/bindings/binding-001/status",
        json={
            "actor_id": "persona-admin-1",
            "actor_role": "persona.admin",
            "status": "revoked",
        },
    )
    assert revoked.status_code == 200, revoked.text
    blocked = test_client.post(
        "/api/rebalances/rb-revoked-before/apply",
        json=_apply_payload(
            rebalance_id="rb-revoked-before",
            command_id="cmd-revoked-before",
        ),
    )
    assert blocked.status_code == 409, blocked.text

    second_binding = test_client.post(
        "/api/bindings",
        json=_binding_payload(
            binding_id="binding-replay",
            capital_sleeve_id="sleeve-replay",
        ),
    )
    assert second_binding.status_code == 201, second_binding.text
    replay_line = [
        {
            "persona_id": "persona-alpha",
            "stage": "live_running",
            "capital_scope": "pool",
            "capital_pool_id": "pool-001",
            "capital_sleeve_id": "sleeve-replay",
            "current_weight": 0.0,
            "target_weight": 0.10,
            "delta": 0.10,
        }
    ]
    assert test_client.post(
        "/api/rebalances",
        json=_rebalance_payload(rebalance_id="rb-replay-after-revoke", lines=replay_line),
    ).status_code == 201
    apply_payload = _apply_payload(
        rebalance_id="rb-replay-after-revoke",
        command_id="cmd-replay-after-revoke",
    )
    applied = test_client.post(
        "/api/rebalances/rb-replay-after-revoke/apply",
        json=apply_payload,
    )
    assert applied.status_code == 200, applied.text
    assert test_client.patch(
        "/api/bindings/binding-replay/status",
        json={
            "actor_id": "persona-admin-1",
            "actor_role": "persona.admin",
            "status": "revoked",
        },
    ).status_code == 200
    replay = test_client.post(
        "/api/rebalances/rb-replay-after-revoke/apply",
        json=apply_payload,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["idempotent_replay"] is True


def test_stale_rebalance_fails_terminal_without_partial_allocation_update(client):
    test_client, _ = client
    _create_default_pool_and_binding(test_client)
    assert test_client.post(
        "/api/bindings",
        json=_binding_payload(
            binding_id="binding-beta",
            persona_id="persona-beta",
            capital_sleeve_id="sleeve-beta",
        ),
    ).status_code == 201
    initial_lines = [
        {
            "persona_id": "persona-alpha",
            "stage": "live_running",
            "capital_scope": "pool",
            "capital_pool_id": "pool-001",
            "capital_sleeve_id": "sleeve-alpha",
            "current_weight": 0.0,
            "target_weight": 0.15,
            "delta": 0.15,
        },
        {
            "persona_id": "persona-beta",
            "stage": "live_running",
            "capital_scope": "pool",
            "capital_pool_id": "pool-001",
            "capital_sleeve_id": "sleeve-beta",
            "current_weight": 0.0,
            "target_weight": 0.25,
            "delta": 0.25,
        },
    ]
    assert test_client.post(
        "/api/rebalances",
        json=_rebalance_payload(rebalance_id="rb-initial", lines=initial_lines),
    ).status_code == 201
    assert test_client.post(
        "/api/rebalances/rb-initial/apply",
        json=_apply_payload(rebalance_id="rb-initial", command_id="cmd-initial"),
    ).status_code == 200

    stale_lines = [
        {
            "persona_id": "persona-alpha",
            "stage": "live_running",
            "capital_scope": "pool",
            "capital_pool_id": "pool-001",
            "capital_sleeve_id": "sleeve-alpha",
            "current_weight": 0.15,
            "target_weight": 0.16,
            "delta": 0.01,
        },
        {
            "persona_id": "persona-beta",
            "stage": "live_running",
            "capital_scope": "pool",
            "capital_pool_id": "pool-001",
            "capital_sleeve_id": "sleeve-beta",
            "current_weight": 0.20,
            "target_weight": 0.21,
            "delta": 0.01,
        },
    ]
    stale_create = test_client.post(
        "/api/rebalances",
        json=_rebalance_payload(rebalance_id="rb-stale", lines=stale_lines),
    )
    assert stale_create.status_code == 201, stale_create.text
    stale_apply = test_client.post(
        "/api/rebalances/rb-stale/apply",
        json=_apply_payload(rebalance_id="rb-stale", command_id="cmd-stale"),
    )
    assert stale_apply.status_code == 409
    assert "no longer match" in stale_apply.json()["detail"]

    failed = test_client.get("/api/rebalances/rb-stale").json()
    assert failed["status"] == "failed"
    assert failed["applied"] is False
    assert failed["failure"]["code"] == "STALE_CURRENT_WEIGHT"
    allocations = {
        item["persona_id"]: item
        for item in test_client.get("/api/allocations").json()["items"]
    }
    assert allocations["persona-alpha"]["current_weight"] == 0.15
    assert allocations["persona-alpha"]["allocation_version"] == 1
    assert allocations["persona-beta"]["current_weight"] == 0.25
    assert allocations["persona-beta"]["allocation_version"] == 1


def test_risk_decreasing_rebalance_does_not_require_approval(client):
    test_client, _ = client
    _create_default_pool_and_binding(test_client)
    bootstrap_line = [
        {
            "persona_id": "persona-alpha",
            "stage": "live_running",
            "capital_scope": "pool",
            "capital_pool_id": "pool-001",
            "capital_sleeve_id": "sleeve-alpha",
            "current_weight": 0.0,
            "target_weight": 0.10,
            "delta": 0.10,
        }
    ]
    assert test_client.post(
        "/api/rebalances",
        json=_rebalance_payload(rebalance_id="rb-decrease-bootstrap", lines=bootstrap_line),
    ).status_code == 201
    assert test_client.post(
        "/api/rebalances/rb-decrease-bootstrap/apply",
        json=_apply_payload(
            rebalance_id="rb-decrease-bootstrap",
            command_id="cmd-decrease-bootstrap",
        ),
    ).status_code == 200
    assert test_client.patch(
        "/api/capital-pools/pool-001/status",
        json={
            "actor_id": "capital-admin-1",
            "actor_role": "capital.admin",
            "status": "suspended",
        },
    ).status_code == 200
    decrease_line = [
        {
            "persona_id": "persona-alpha",
            "stage": "live_running",
            "capital_scope": "pool",
            "capital_pool_id": "pool-001",
            "capital_sleeve_id": "sleeve-alpha",
            "current_weight": 0.10,
            "target_weight": 0.05,
            "delta": -0.05,
        }
    ]
    assert test_client.post(
        "/api/rebalances",
        json=_rebalance_payload(rebalance_id="rb-decrease", lines=decrease_line),
    ).status_code == 201
    applied = test_client.post(
        "/api/rebalances/rb-decrease/apply",
        json=_apply_payload(
            rebalance_id="rb-decrease",
            command_id="cmd-decrease",
            approval_ref=None,
        ),
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["approval_ref"] is None
    assert applied.json()["allocation_readback"][0]["current_weight"] == 0.05


def test_containment_is_durable_frozen_and_cannot_promote_or_increase(client):
    test_client, _ = client
    _create_default_pool_and_binding(test_client)
    flat_line = [
        {
            "persona_id": "persona-alpha",
            "stage": "live_running",
            "capital_scope": "pool",
            "capital_pool_id": "pool-001",
            "capital_sleeve_id": "sleeve-alpha",
            "current_weight": 0.0,
            "target_weight": 0.10,
            "delta": 0.10,
        }
    ]
    assert test_client.post(
        "/api/rebalances",
        json=_rebalance_payload(rebalance_id="rb-baseline", lines=flat_line),
    ).status_code == 201
    assert test_client.post(
        "/api/rebalances/rb-baseline/apply",
        json=_apply_payload(
            rebalance_id="rb-baseline",
            command_id="cmd-baseline",
        ),
    ).status_code == 200

    containment_payload = {
        "actor_id": "operator-1",
        "actor_role": "operator",
        "idempotency_key": "containment-freeze-1",
        "request_hash": "request-containment-freeze-1",
        "containment_id": "containment-freeze-1",
        "persona_id": "persona-alpha",
        "capital_pool_id": "pool-001",
        "action": "freeze",
        "trigger": "hard_risk_breach",
        "evidence_refs": ["risk-event:42"],
        "current_weight": 0.10,
        "target_weight": 0.10,
        "command_id": "cmd-containment-1",
        "two_man_signature_id": "two-man-1",
    }
    frozen = test_client.post("/api/containments", json=containment_payload)
    assert frozen.status_code == 201, frozen.text
    assert frozen.json()["state"] == "frozen"
    assert frozen.json()["containment_state"] == "frozen"
    assert frozen.json()["status"] == "executed"
    assert frozen.json()["authoritative_containment_readback"] is True
    assert frozen.json()["authoritative_capital_state_applied"] is True
    assert frozen.json()["live_capital_side_effects"] is False
    containment_lookup = test_client.get(
        "/api/containments/receipts/cmd-containment-1"
    )
    assert containment_lookup.status_code == 200, containment_lookup.text
    assert containment_lookup.json()["containment_id"] == "containment-freeze-1"

    listed = test_client.get(
        "/api/containments",
        params={"persona_id": "persona-alpha"},
    )
    assert listed.status_code == 200
    assert [item["containment_id"] for item in listed.json()] == ["containment-freeze-1"]
    allocation = test_client.get(
        "/api/allocations",
        params={"persona_id": "persona-alpha"},
    ).json()["items"][0]
    assert allocation["containment_state"] == "frozen"
    assert allocation["current_weight"] == 0.10

    replay = test_client.post("/api/containments", json=containment_payload)
    assert replay.status_code == 201
    assert replay.json()["containment_id"] == "containment-freeze-1"
    assert replay.json()["idempotent_replay"] is True

    forbidden = test_client.post(
        "/api/containments",
        json={
            **containment_payload,
            "containment_id": "containment-promote",
            "idempotency_key": "containment-promote",
            "request_hash": "request-containment-promote",
            "command_id": "cmd-containment-promote",
            "action": "promote_to_live",
        },
    )
    assert forbidden.status_code == 422
    assert "cannot promote or increase" in forbidden.json()["detail"]

    increase = test_client.post(
        "/api/containments",
        json={
            **containment_payload,
            "containment_id": "containment-increase",
            "idempotency_key": "containment-increase",
            "request_hash": "request-containment-increase",
            "command_id": "cmd-containment-increase",
            "target_weight": 0.11,
        },
    )
    assert increase.status_code == 422
    assert len(test_client.get("/api/containments").json()) == 1

    restarted = TestClient(_reload_capital_module().app)
    assert restarted.get("/api/containments").json()[0]["state"] == "frozen"


def test_rebalance_audit_failure_is_nonfatal_and_reconciles_on_replay(client):
    test_client, _ = client
    _create_default_pool_and_binding(test_client)
    assert test_client.post(
        "/api/rebalances",
        json=_rebalance_payload(rebalance_id="rb-audit-retry"),
    ).status_code == 201

    module = importlib.import_module("services.capital.main")
    original_audit_store = module.audit_store

    class FailingAuditStore:
        def append_event(self, **kwargs):
            raise RuntimeError("audit sink unavailable")

        def list_events(self, **kwargs):
            return []

    module.audit_store = FailingAuditStore()
    apply_payload = _apply_payload(
        rebalance_id="rb-audit-retry",
        command_id="cmd-audit-retry",
    )
    applied = test_client.post(
        "/api/rebalances/rb-audit-retry/apply",
        json=apply_payload,
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["audit_delivery_status"] == "pending"
    assert applied.json()["audit_delivery_attempts"] == 1
    assert "audit sink unavailable" in applied.json()["audit_delivery_error"]

    module.audit_store = original_audit_store
    replay = test_client.post(
        "/api/rebalances/rb-audit-retry/apply",
        json=apply_payload,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["audit_delivery_status"] == "delivered"
    assert replay.json()["audit_delivery_attempts"] == 2
    lookup = test_client.get("/api/rebalances/receipts/cmd-audit-retry")
    assert lookup.status_code == 200
    assert lookup.json()["audit_delivery_status"] == "delivered"

"""EVOCHAIN-004 contract tests for governance freeze/rollback read stores."""
from __future__ import annotations

from fastapi.testclient import TestClient

from services.governance import main
from services.governance import record_store as record_store_module
from services.governance.record_store import JsonGovernanceRecordStore, build_governance_record_store


def _isolated_client(tmp_path, monkeypatch) -> tuple[TestClient, JsonGovernanceRecordStore, JsonGovernanceRecordStore]:
    freeze_store = JsonGovernanceRecordStore(
        tmp_path / "freeze_orders.json",
        id_fields=("freeze_order_id", "id"),
    )
    rollback_store = JsonGovernanceRecordStore(
        tmp_path / "rollbacks.json",
        id_fields=("rollback_id", "id"),
    )
    monkeypatch.setattr(main, "freeze_order_store", freeze_store)
    monkeypatch.setattr(main, "rollback_store", rollback_store)
    return TestClient(main.app), freeze_store, rollback_store


def test_empty_canonical_stores_return_healthy_empty_lists(tmp_path, monkeypatch) -> None:
    client, _, _ = _isolated_client(tmp_path, monkeypatch)

    freeze_response = client.get("/api/governance/freeze-orders")
    rollback_response = client.get("/api/governance/rollbacks")

    assert freeze_response.status_code == 200
    assert freeze_response.json() == []
    assert rollback_response.status_code == 200
    assert rollback_response.json() == []


def test_populated_stores_support_sorted_list_filter_and_detail_reads(tmp_path, monkeypatch) -> None:
    client, freeze_store, rollback_store = _isolated_client(tmp_path, monkeypatch)
    freeze_store.put(
        {
            "freeze_order_id": "freeze-older",
            "scope": "persona",
            "target_id": "persona-alpha",
            "status": "released",
            "created_at": "2026-07-13T08:00:00Z",
        }
    )
    freeze_store.put(
        {
            "freeze_order_id": "freeze-active",
            "scope": "persona",
            "target_id": "persona-beta",
            "status": "active",
            "created_at": "2026-07-13T09:00:00Z",
        }
    )
    rollback_store.put(
        {
            "rollback_id": "rollback-older",
            "runtime_id": "runtime-alpha",
            "action_type": "replace",
            "status": "completed",
            "initiated_at": "2026-07-13T08:30:00Z",
        }
    )
    rollback_store.put(
        {
            "rollback_id": "rollback-latest",
            "runtime_id": "runtime-beta",
            "action_type": "pause_then_replace",
            "status": "accepted",
            "initiated_at": "2026-07-13T09:30:00Z",
        }
    )

    freeze_list = client.get("/api/governance/freeze-orders").json()
    assert [record["freeze_order_id"] for record in freeze_list] == ["freeze-active", "freeze-older"]
    assert client.get(
        "/api/governance/freeze-orders",
        params={"status": "active", "scope": "persona"},
    ).json() == [freeze_list[0]]
    assert client.get("/api/governance/freeze-orders/freeze-active").json() == freeze_list[0]

    rollback_list = client.get("/api/governance/rollbacks").json()
    assert [record["rollback_id"] for record in rollback_list] == ["rollback-latest", "rollback-older"]
    assert client.get(
        "/api/governance/rollbacks",
        params={"runtime_id": "runtime-beta", "action_type": "pause_then_replace"},
    ).json() == [rollback_list[0]]
    assert client.get("/api/governance/rollbacks/rollback-latest").json() == rollback_list[0]

    assert client.get("/api/governance/freeze-orders/missing").status_code == 404
    assert client.get("/api/governance/rollbacks/missing").status_code == 404

    reloaded_freezes = JsonGovernanceRecordStore(
        tmp_path / "freeze_orders.json",
        id_fields=("freeze_order_id", "id"),
    )
    reloaded_rollbacks = JsonGovernanceRecordStore(
        tmp_path / "rollbacks.json",
        id_fields=("rollback_id", "id"),
    )
    assert reloaded_freezes.get("freeze-active") == freeze_list[0]
    assert reloaded_rollbacks.get("rollback-latest") == rollback_list[0]


def test_postgres_posture_builds_service_owned_dataset_tables(tmp_path, monkeypatch) -> None:
    class FakePostgresJsonOwnerStore:
        instances = []

        def __init__(self, *, dsn, table, owner_service, bootstrap):
            self.dsn = dsn
            self.table = table
            self.owner_service = owner_service
            self.bootstrap = bootstrap
            self.records = {}
            type(self).instances.append(self)

        def put(self, record_id, payload):
            self.records[record_id] = payload

        def get(self, record_id):
            return self.records.get(record_id)

        def list_all(self):
            return list(self.records.values())

    monkeypatch.setenv("GOVERNANCE_STORE_BACKEND", "postgres")
    monkeypatch.setenv("GOVERNANCE_STORE_DSN", "postgresql://governance-owner/pantheon")
    monkeypatch.setattr(record_store_module, "PostgresJsonOwnerStore", FakePostgresJsonOwnerStore)

    freeze_store = build_governance_record_store(
        tmp_path / "unused-freeze.json",
        table="governance.freeze_orders",
        id_fields=("freeze_order_id", "id"),
    )
    rollback_store = build_governance_record_store(
        tmp_path / "unused-rollback.json",
        table="governance.rollbacks",
        id_fields=("rollback_id", "id"),
    )
    freeze_store.put({"freeze_order_id": "freeze-pg", "status": "active"})
    rollback_store.put({"rollback_id": "rollback-pg", "status": "accepted"})

    assert [instance.table for instance in FakePostgresJsonOwnerStore.instances] == [
        "governance.freeze_orders",
        "governance.rollbacks",
    ]
    assert all(instance.owner_service == "governance-svc" for instance in FakePostgresJsonOwnerStore.instances)
    assert freeze_store.get("freeze-pg") == {"freeze_order_id": "freeze-pg", "status": "active"}
    assert rollback_store.get("rollback-pg") == {"rollback_id": "rollback-pg", "status": "accepted"}


def test_post_endpoints_persist_correctly(tmp_path, monkeypatch) -> None:
    client, freeze_store, rollback_store = _isolated_client(tmp_path, monkeypatch)

    freeze_payload = {
        "freeze_order_id": "freeze-post-test",
        "scope": "persona",
        "target_id": "persona-gamma",
        "status": "active",
        "actor": "admin",
        "source_command_id": "cmd-123",
        "reason": "Test freeze order post",
    }
    response = client.post(
        "/api/governance/freeze-orders",
        json=freeze_payload,
        headers={"Authorization": "Bearer op-test:admin"},
    )
    assert response.status_code == 201
    res_body = response.json()
    assert res_body["freeze_order_id"] == "freeze-post-test"
    assert res_body["status"] == "active"
    # identity is derived from the authenticated token, not the request body.
    assert res_body["identity"] == "op-test"
    assert freeze_store.get("freeze-post-test")["reason"] == "Test freeze order post"

    rollback_payload = {
        "rollback_id": "rollback-post-test",
        "runtime_id": "runtime-gamma",
        "action_type": "replace",
        "status": "completed",
        "actor": "operator",
        "source_command_id": "cmd-456",
    }
    response2 = client.post(
        "/api/governance/rollbacks",
        json=rollback_payload,
        headers={"Authorization": "Bearer op-test:operator"},
    )
    assert response2.status_code == 201
    res_body2 = response2.json()
    assert res_body2["rollback_id"] == "rollback-post-test"
    assert res_body2["status"] == "completed"
    assert res_body2["identity"] == "op-test"
    assert rollback_store.get("rollback-post-test")["runtime_id"] == "runtime-gamma"


def test_post_endpoints_require_authentication(tmp_path, monkeypatch) -> None:
    """Unauthenticated writes must be rejected, not silently persisted (EVOCHAIN-005 round 2)."""
    client, freeze_store, rollback_store = _isolated_client(tmp_path, monkeypatch)

    freeze_response = client.post(
        "/api/governance/freeze-orders",
        json={
            "freeze_order_id": "freeze-unauth",
            "scope": "persona",
            "target_id": "persona-gamma",
            "status": "active",
            "actor": "admin",
            "identity": "attacker",
            "source_command_id": "cmd-unauth",
        },
    )
    assert freeze_response.status_code == 401
    assert freeze_store.get("freeze-unauth") is None

    rollback_response = client.post(
        "/api/governance/rollbacks",
        json={
            "rollback_id": "rollback-unauth",
            "runtime_id": "runtime-gamma",
            "action_type": "replace",
            "status": "approved",
            "actor": "admin",
            "identity": "attacker",
            "source_command_id": "cmd-unauth",
        },
    )
    assert rollback_response.status_code == 401
    assert rollback_store.get("rollback-unauth") is None


def test_post_endpoints_reject_role_spoofing_and_self_declared_approval(tmp_path, monkeypatch) -> None:
    """A caller cannot self-declare an authority role/status it does not hold (EVOCHAIN-005 round 2)."""
    client, freeze_store, rollback_store = _isolated_client(tmp_path, monkeypatch)

    # Authenticated as a plain operator, but declaring "admin" for the actor
    # field — the token only carries "operator", so this must be rejected.
    spoof_response = client.post(
        "/api/governance/freeze-orders",
        json={
            "freeze_order_id": "freeze-spoof",
            "scope": "persona",
            "target_id": "persona-gamma",
            "status": "active",
            "actor": "admin",
            "identity": "attacker",
            "source_command_id": "cmd-spoof",
        },
        headers={"Authorization": "Bearer op-test:operator"},
    )
    assert spoof_response.status_code == 403
    assert freeze_store.get("freeze-spoof") is None

    # An authenticated but unprivileged (operator-only) caller cannot create a
    # rollback record that is already "approved" — only a
    # _GOVERNANCE_AUTHORITY_ROLES-level role may set that status, whether on
    # create or on a later transition.
    unauth_status_response = client.post(
        "/api/governance/rollbacks",
        json={
            "rollback_id": "rollback-self-approved",
            "runtime_id": "runtime-gamma",
            "action_type": "replace",
            "status": "approved",
            "source_command_id": "cmd-self-approve",
        },
        headers={"Authorization": "Bearer op-test:operator"},
    )
    assert unauth_status_response.status_code == 403
    assert rollback_store.get("rollback-self-approved") is None


def test_freeze_order_status_transitions(tmp_path, monkeypatch) -> None:
    """EVOCHAIN-005: Enforce legal state transitions for FreezeOrders."""
    client, freeze_store, rollback_store = _isolated_client(tmp_path, monkeypatch)

    # 1. Create a freeze order as operator (allowed by _FREEZE_CREATE_AUTHORITY_ROLES)
    resp = client.post(
        "/api/governance/freeze-orders",
        json={
            "freeze_order_id": "freeze-transition-test",
            "scope": "persona",
            "target_id": "persona-gamma",
            "status": "active",
            "actor": "operator",
            "source_command_id": "cmd-init-ks",
        },
        headers={"Authorization": "Bearer op-test:operator"},
    )
    assert resp.status_code == 201

    # 2. Transition from active to released (allowed)
    resp2 = client.post(
        "/api/governance/freeze-orders",
        json={
            "freeze_order_id": "freeze-transition-test",
            "status": "released",
            "actor": "governance_reviewer",
            "source_command_id": "cmd-release-ks",
        },
        headers={"Authorization": "Bearer reviewer-test:governance_reviewer"},
    )
    assert resp2.status_code == 200

    # 3. Transition from terminal state released to active (forbidden)
    resp3 = client.post(
        "/api/governance/freeze-orders",
        json={
            "freeze_order_id": "freeze-transition-test",
            "status": "active",
            "actor": "governance_reviewer",
            "source_command_id": "cmd-reactivate-ks",
        },
        headers={"Authorization": "Bearer reviewer-test:governance_reviewer"},
    )
    assert resp3.status_code == 400


def test_rollback_status_transitions(tmp_path, monkeypatch) -> None:
    """EVOCHAIN-005: Enforce legal state transitions for Rollback records."""
    client, freeze_store, rollback_store = _isolated_client(tmp_path, monkeypatch)

    # 1. Create as initiated (allowed)
    resp = client.post(
        "/api/governance/rollbacks",
        json={
            "rollback_id": "rollback-transition-test",
            "runtime_id": "runtime-gamma",
            "action_type": "replace",
            "status": "initiated",
            "actor": "operator",
            "source_command_id": "cmd-init-rb",
        },
        headers={"Authorization": "Bearer op-test:operator"},
    )
    assert resp.status_code == 201

    # 2. Transition from initiated to approved (allowed)
    resp2 = client.post(
        "/api/governance/rollbacks",
        json={
            "rollback_id": "rollback-transition-test",
            "status": "approved",
            "actor": "approver",
            "source_command_id": "cmd-approve-rb",
        },
        headers={"Authorization": "Bearer approver-test:approver"},
    )
    assert resp2.status_code == 200

    # 3. Transition from approved to completed (allowed)
    resp3 = client.post(
        "/api/governance/rollbacks",
        json={
            "rollback_id": "rollback-transition-test",
            "status": "completed",
            "actor": "operator",
            "source_command_id": "cmd-complete-rb",
        },
        headers={"Authorization": "Bearer op-test:operator"},
    )
    assert resp3.status_code == 200

    # 4. Transition from terminal state completed to initiated (forbidden)
    resp4 = client.post(
        "/api/governance/rollbacks",
        json={
            "rollback_id": "rollback-transition-test",
            "status": "initiated",
            "actor": "operator",
            "source_command_id": "cmd-reinit-rb",
        },
        headers={"Authorization": "Bearer op-test:operator"},
    )
    assert resp4.status_code == 400

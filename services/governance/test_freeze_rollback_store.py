"""EVOCHAIN-004 contract tests for governance freeze/rollback read stores."""
from __future__ import annotations

import pytest
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


def test_freeze_and_rollback_stores_multi_instance_isolation(tmp_path, monkeypatch) -> None:
    """Closes F09: verify independent instances of freeze and rollback stores coordinate without lost updates."""
    freeze_path = tmp_path / "freeze_orders.json"
    rollback_path = tmp_path / "rollbacks.json"

    freeze_store_a = JsonGovernanceRecordStore(freeze_path, id_fields=("freeze_order_id", "id"))
    freeze_store_b = JsonGovernanceRecordStore(freeze_path, id_fields=("freeze_order_id", "id"))
    rollback_store_a = JsonGovernanceRecordStore(rollback_path, id_fields=("rollback_id", "id"))
    rollback_store_b = JsonGovernanceRecordStore(rollback_path, id_fields=("rollback_id", "id"))

    # Instance A writes freeze 1
    freeze_store_a.put(
        {
            "freeze_order_id": "freeze-iso-1",
            "scope": "persona",
            "target_id": "p-1",
            "status": "active",
            "actor": "admin",
            "identity": "admin-1",
            "source_command_id": "cmd-1",
        }
    )

    # Instance B writes freeze 2
    freeze_store_b.put(
        {
            "freeze_order_id": "freeze-iso-2",
            "scope": "persona",
            "target_id": "p-2",
            "status": "requested",
            "actor": "operator",
            "identity": "op-2",
            "source_command_id": "cmd-2",
        }
    )

    # Instance A writes rollback 1
    rollback_store_a.put(
        {
            "rollback_id": "rb-iso-1",
            "runtime_id": "rt-1",
            "action_type": "replace",
            "status": "completed",
            "actor": "operator",
            "identity": "op-1",
            "source_command_id": "cmd-3",
        }
    )

    # Instance B writes rollback 2
    rollback_store_b.put(
        {
            "rollback_id": "rb-iso-2",
            "runtime_id": "rt-2",
            "action_type": "pause",
            "status": "initiated",
            "actor": "operator",
            "identity": "op-2",
            "source_command_id": "cmd-4",
        }
    )

    # Both instances observe both freeze orders and rollbacks
    assert freeze_store_a.get("freeze-iso-2") is not None
    assert freeze_store_b.get("freeze-iso-1") is not None
    assert len(freeze_store_a.list_all()) == 2
    assert len(freeze_store_b.list_all()) == 2

    assert rollback_store_a.get("rb-iso-2") is not None
    assert rollback_store_b.get("rb-iso-1") is not None
    assert len(rollback_store_a.list_all()) == 2
    assert len(rollback_store_b.list_all()) == 2

    # Fresh instance verification
    fresh_freezes = JsonGovernanceRecordStore(freeze_path, id_fields=("freeze_order_id", "id"))
    fresh_rollbacks = JsonGovernanceRecordStore(rollback_path, id_fields=("rollback_id", "id"))
    assert {r["freeze_order_id"] for r in fresh_freezes.list_all()} == {"freeze-iso-1", "freeze-iso-2"}
    assert {r["rollback_id"] for r in fresh_rollbacks.list_all()} == {"rb-iso-1", "rb-iso-2"}


def test_mounted_freeze_and_rollback_command_isolation_interleaved(tmp_path, monkeypatch) -> None:
    """Mounted handlers exercise command locks, legal transitions, and terminal conflict rejection across independent store instances."""
    freeze_path = tmp_path / "mounted_freeze.json"
    rollback_path = tmp_path / "mounted_rollback.json"

    freeze_a = JsonGovernanceRecordStore(freeze_path, id_fields=("freeze_order_id", "id"))
    freeze_b = JsonGovernanceRecordStore(freeze_path, id_fields=("freeze_order_id", "id"))
    rb_a = JsonGovernanceRecordStore(rollback_path, id_fields=("rollback_id", "id"))
    rb_b = JsonGovernanceRecordStore(rollback_path, id_fields=("rollback_id", "id"))

    # 1. Freeze order controlled interleaving across instances
    monkeypatch.setattr(main, "freeze_order_store", freeze_a)
    f_create = main.record_freeze_order(
        body={
            "freeze_order_id": "freeze-mount-intl-1",
            "scope": "persona",
            "target_id": "persona-gamma",
            "status": "requested",
            "actor": "operator",
            "source_command_id": "cmd-f-init",
        },
        authorization="Bearer op-test:operator",
        x_mfa_token=None,
    )
    assert f_create["status"] == "requested"

    # Instance B reads and transitions to active
    monkeypatch.setattr(main, "freeze_order_store", freeze_b)
    f_act = main.record_freeze_order(
        body={
            "freeze_order_id": "freeze-mount-intl-1",
            "status": "active",
            "actor": "governance_reviewer",
            "source_command_id": "cmd-f-act",
            "transition_actor": "governance_reviewer",
            "transition_source_command_id": "cmd-f-act",
        },
        authorization="Bearer rev-test:governance_reviewer",
        x_mfa_token=None,
    )
    assert f_act["status"] == "active"

    # Instance A reads and transitions to released (terminal)
    monkeypatch.setattr(main, "freeze_order_store", freeze_a)
    f_rel = main.record_freeze_order(
        body={
            "freeze_order_id": "freeze-mount-intl-1",
            "status": "released",
            "actor": "admin",
            "source_command_id": "cmd-f-rel",
            "transition_actor": "admin",
            "transition_source_command_id": "cmd-f-rel",
        },
        authorization="Bearer admin-test:admin",
        x_mfa_token=None,
    )
    assert f_rel["status"] == "released"

    # Instance B attempts transition from terminal status 'released' -> 400 rejection
    monkeypatch.setattr(main, "freeze_order_store", freeze_b)
    with pytest.raises(main.HTTPException) as exc_info:
        main.record_freeze_order(
            body={
                "freeze_order_id": "freeze-mount-intl-1",
                "status": "active",
                "actor": "operator",
                "source_command_id": "cmd-f-reopen",
                "transition_actor": "operator",
                "transition_source_command_id": "cmd-f-reopen",
            },
            authorization="Bearer op-test:operator",
            x_mfa_token=None,
        )
    assert exc_info.value.status_code == 400
    assert "Cannot transition from terminal freeze order status 'released'" in str(exc_info.value.detail)

    # 2. Rollback record controlled interleaving across instances
    monkeypatch.setattr(main, "rollback_store", rb_a)
    rb_create = main.record_rollback(
        body={
            "rollback_id": "rb-mount-intl-1",
            "runtime_id": "runtime-intl-1",
            "action_type": "replace",
            "status": "initiated",
            "actor": "operator",
            "source_command_id": "cmd-rb-init",
        },
        authorization="Bearer op-test:operator",
        x_mfa_token=None,
    )
    assert rb_create["status"] == "initiated"

    # Instance B transitions to approved
    monkeypatch.setattr(main, "rollback_store", rb_b)
    rb_appr = main.record_rollback(
        body={
            "rollback_id": "rb-mount-intl-1",
            "status": "approved",
            "actor": "approver",
            "source_command_id": "cmd-rb-appr",
            "transition_actor": "approver",
            "transition_source_command_id": "cmd-rb-appr",
        },
        authorization="Bearer appr-test:approver",
        x_mfa_token=None,
    )
    assert rb_appr["status"] == "approved"

    # Instance A transitions to completed (terminal)
    monkeypatch.setattr(main, "rollback_store", rb_a)
    rb_comp = main.record_rollback(
        body={
            "rollback_id": "rb-mount-intl-1",
            "status": "completed",
            "actor": "operator",
            "source_command_id": "cmd-rb-comp",
            "transition_actor": "operator",
            "transition_source_command_id": "cmd-rb-comp",
        },
        authorization="Bearer op-test:operator",
        x_mfa_token=None,
    )
    assert rb_comp["status"] == "completed"

    # Instance B attempts transition from terminal status 'completed' -> 400 rejection
    monkeypatch.setattr(main, "rollback_store", rb_b)
    with pytest.raises(main.HTTPException) as exc_rb_info:
        main.record_rollback(
            body={
                "rollback_id": "rb-mount-intl-1",
                "status": "approved",
                "actor": "operator",
                "source_command_id": "cmd-rb-reopen",
                "transition_actor": "operator",
                "transition_source_command_id": "cmd-rb-reopen",
            },
            authorization="Bearer op-test:operator",
            x_mfa_token=None,
        )
    assert exc_rb_info.value.status_code == 400
    assert "Cannot transition from terminal rollback status 'completed'" in str(exc_rb_info.value.detail)

    # 3. Durable rereads from fresh instance C verify all original and transition audit fields
    freeze_c = JsonGovernanceRecordStore(freeze_path, id_fields=("freeze_order_id", "id"))
    final_freeze = freeze_c.get("freeze-mount-intl-1")
    assert final_freeze["status"] == "released"
    assert final_freeze["scope"] == "persona"
    assert final_freeze["target_id"] == "persona-gamma"
    assert final_freeze["actor"] == "operator"
    assert final_freeze["source_command_id"] == "cmd-f-init"
    assert final_freeze["transition_actor"] == "admin"
    assert final_freeze["transition_source_command_id"] == "cmd-f-rel"

    rb_c = JsonGovernanceRecordStore(rollback_path, id_fields=("rollback_id", "id"))
    final_rb = rb_c.get("rb-mount-intl-1")
    assert final_rb["status"] == "completed"
    assert final_rb["runtime_id"] == "runtime-intl-1"
    assert final_rb["action_type"] == "replace"
    assert final_rb["actor"] == "operator"
    assert final_rb["source_command_id"] == "cmd-rb-init"
    assert final_rb["transition_actor"] == "operator"
    assert final_rb["transition_source_command_id"] == "cmd-rb-comp"

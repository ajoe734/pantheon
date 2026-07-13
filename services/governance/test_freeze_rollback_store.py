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

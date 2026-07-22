from __future__ import annotations

import sys
import threading
from types import SimpleNamespace
from unittest import mock

import pytest

from agora.strategy_workshop.store import MemoryWorkshopStore, PostgresWorkshopStore


def _store(*, status: str = "open") -> MemoryWorkshopStore:
    store = MemoryWorkshopStore()
    store.create_session(
        {
            "workshop_id": "ws-command-1",
            "tenant_id": "tenant-a",
            "user_id": "operator-a",
            "strategy_id": "strategy-a",
            "status": status,
        }
    )
    return store


def _admit(store: MemoryWorkshopStore, **overrides):
    params = {
        "workshop_id": "ws-command-1",
        "tenant_id": "tenant-a",
        "user_id": "operator-a",
        "operation": "create_version",
        "idempotency_key": "idem-version-1",
        "request_hash": "sha256:request-a",
        "expected_lock_version": 1,
        "request_payload": {
            "reason": "refine alpha thesis",
            "authorization": "Bearer must-not-persist",
            "nested": {"mfa_token": "must-not-persist"},
        },
        "request_id": "request-1",
        "trace_id": "trace-1",
    }
    params.update(overrides)
    return store.admit_command(**params)


def test_admit_is_rpo_zero_and_exact_replay_precedes_etag_check() -> None:
    store = _store()

    admitted = _admit(store)
    assert admitted["outcome"] == "admitted"
    assert admitted["current_lock_version"] == 2
    receipt = admitted["receipt"]
    assert receipt["status"] == "admitted"
    assert receipt["expected_lock_version"] == 1
    assert receipt["admitted_lock_version"] == 2
    assert receipt["resulting_lock_version"] == 2
    assert receipt["request_id"] == "request-1"
    assert receipt["trace_id"] == "trace-1"
    assert receipt["request_payload"]["reason"] == "[REDACTED]"
    assert receipt["request_payload"]["authorization"] == "[REDACTED]"
    assert receipt["request_payload"]["nested"]["mfa_token"] == "[REDACTED]"
    assert store.get_session("ws-command-1")["lock_version"] == 2
    assert store.get_command_receipt(
        workshop_id="ws-command-1",
        tenant_id="tenant-a",
        user_id="operator-a",
        operation="create_version",
        idempotency_key="idem-version-1",
    )["command_id"] == receipt["command_id"]

    # The original If-Match is stale now, but an exact replay must still recover
    # the receipt and must not advance the lock a second time.
    replay = _admit(store, expected_lock_version=1)
    assert replay["outcome"] == "replay"
    assert replay["receipt"]["command_id"] == receipt["command_id"]
    assert store.get_session("ws-command-1")["lock_version"] == 2


def test_idempotency_payload_conflict_does_not_mutate_receipt_or_lock() -> None:
    store = _store()
    admitted = _admit(store)

    conflict = _admit(store, request_hash="sha256:different", expected_lock_version=2)

    assert conflict["outcome"] == "idempotency_conflict"
    assert conflict["receipt"]["command_id"] == admitted["receipt"]["command_id"]
    assert conflict["receipt"]["request_hash"] == "sha256:request-a"
    assert store.get_session("ws-command-1")["lock_version"] == 2


def test_stale_terminal_and_scope_failures_are_not_admitted() -> None:
    stale_store = _store()
    stale = _admit(stale_store, expected_lock_version=99)
    assert stale == {
        "outcome": "stale",
        "receipt": None,
        "current_lock_version": 1,
    }
    assert stale_store.get_command_receipt(
        workshop_id="ws-command-1",
        tenant_id="tenant-a",
        user_id="operator-a",
        operation="create_version",
        idempotency_key="idem-version-1",
    ) is None

    terminal_store = _store(status="concluded")
    terminal = _admit(terminal_store)
    assert terminal["outcome"] == "terminal"
    assert terminal["workshop_status"] == "concluded"
    assert terminal_store.get_session("ws-command-1")["lock_version"] == 1

    scoped_store = _store()
    scoped = _admit(scoped_store, tenant_id="tenant-b")
    assert scoped["outcome"] == "scope_mismatch"
    assert scoped_store.get_session("ws-command-1")["lock_version"] == 1


def test_complete_atomically_records_version_session_event_and_readback() -> None:
    store = _store()
    _admit(store)
    authoritative_result = {
        "workshop_version_id": "wv-2",
        "strategy_spec_registry_id": "registry-2",
        "state": "active",
    }

    completed = store.complete_command(
        workshop_id="ws-command-1",
        tenant_id="tenant-a",
        user_id="operator-a",
        operation="create_version",
        idempotency_key="idem-version-1",
        request_hash="sha256:request-a",
        result=authoritative_result,
        canonical_refs={"strategy_spec_registry_id": "registry-2"},
        version_link={
            "workshop_version_id": "wv-2",
            "strategy_id": "strategy-a",
            "strategy_spec_registry_id": "registry-2",
            "parent_workshop_version_id": "wv-1",
            "document_sha256": "a" * 64,
            "created_by": "operator-a",
        },
        session_updates={
            "active_strategy_spec_registry_id": "registry-2",
            "active_workshop_version_id": "wv-2",
        },
        event={
            "event_id": "event-version-2",
            "actor_type": "operator",
            "event_type": "version_created",
            "redacted_summary": "Created immutable version link",
            "payload_refs_json": ["registry-2"],
        },
    )

    assert completed["outcome"] == "completed"
    assert completed["receipt"]["status"] == "completed"
    assert completed["receipt"]["result"] == authoritative_result
    assert completed["receipt"]["canonical_refs"] == {
        "strategy_spec_registry_id": "registry-2"
    }
    assert completed["version_link"]["sequence_no"] == 1
    assert completed["version_link"]["document_sha256"] == "a" * 64
    assert store.get_version_link("ws-command-1", "wv-2") == completed["version_link"]
    assert store.list_version_links("ws-command-1") == [completed["version_link"]]
    session = store.get_session("ws-command-1")
    assert session["lock_version"] == 2
    assert session["active_strategy_spec_registry_id"] == "registry-2"
    assert session["active_workshop_version_id"] == "wv-2"
    assert session["selected_version_id"] == "wv-2"
    assert [event["event_id"] for event in store.list_events("ws-command-1")] == [
        "event-version-2"
    ]

    replay = store.complete_command(
        workshop_id="ws-command-1",
        tenant_id="tenant-a",
        user_id="operator-a",
        operation="create_version",
        idempotency_key="idem-version-1",
        request_hash="sha256:request-a",
        result={"ignored": True},
    )
    assert replay["outcome"] == "replay"
    assert replay["receipt"]["result"] == authoritative_result
    assert len(store.list_version_links("ws-command-1")) == 1
    assert len(store.list_events("ws-command-1")) == 1


def test_failure_and_compensation_are_durable_without_lock_rollback() -> None:
    store = _store()
    admitted = _admit(store)

    failed = store.fail_command(
        workshop_id="ws-command-1",
        tenant_id="tenant-a",
        user_id="operator-a",
        operation="create_version",
        idempotency_key="idem-version-1",
        request_hash="sha256:request-a",
        failure={"code": "REGISTRY_UNAVAILABLE", "retryable": True},
        compensation={"state": "no_external_write_observed"},
        event={
            "event_id": "event-version-failed",
            "actor_type": "system",
            "event_type": "command_failed",
            "redacted_summary": "Registry was unavailable",
        },
    )

    assert failed["outcome"] == "failed"
    assert failed["receipt"]["command_id"] == admitted["receipt"]["command_id"]
    assert failed["receipt"]["failure"]["code"] == "REGISTRY_UNAVAILABLE"
    assert failed["receipt"]["compensation"] == {
        "state": "no_external_write_observed"
    }
    assert store.get_session("ws-command-1")["lock_version"] == 2

    replay = _admit(store)
    assert replay["outcome"] == "replay"
    assert replay["receipt"]["status"] == "failed"
    assert store.get_session("ws-command-1")["lock_version"] == 2


def test_concurrent_same_key_has_one_admission_and_one_replay() -> None:
    store = _store()
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def worker() -> None:
        barrier.wait()
        outcomes.append(_admit(store)["outcome"])

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["admitted", "replay"]
    assert store.get_session("ws-command-1")["lock_version"] == 2


def test_session_update_columns_are_allowlisted() -> None:
    store = _store()
    _admit(store)

    with pytest.raises(ValueError, match="Unsupported session update fields"):
        store.complete_command(
            workshop_id="ws-command-1",
            tenant_id="tenant-a",
            user_id="operator-a",
            operation="create_version",
            idempotency_key="idem-version-1",
            request_hash="sha256:request-a",
            result={},
            session_updates={"status = 'archived'; DROP TABLE x; --": "open"},
        )
    assert store.get_command_receipt(
        workshop_id="ws-command-1",
        tenant_id="tenant-a",
        user_id="operator-a",
        operation="create_version",
        idempotency_key="idem-version-1",
    )["status"] == "admitted"


def test_runtime_postgres_bootstrap_contains_additive_operation_tables() -> None:
    class FakeConnection:
        def __init__(self) -> None:
            self.statements: list[str] = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def execute(self, statement: str, *args, **kwargs):
            self.statements.append(" ".join(statement.split()))
            return SimpleNamespace(fetchone=lambda: None, fetchall=lambda: [])

    connection = FakeConnection()
    fake_psycopg = SimpleNamespace(connect=lambda dsn: connection)
    with mock.patch.dict(sys.modules, {"psycopg": fake_psycopg}):
        PostgresWorkshopStore(
            dsn="postgresql://example/pantheon",
            schema="agora_command_test",
        )

    ddl = "\n".join(connection.statements)
    assert 'CREATE TABLE IF NOT EXISTS "agora_command_test"."strategy_workshop_version_link"' in ddl
    assert 'CREATE TABLE IF NOT EXISTS "agora_command_test"."strategy_workshop_command_receipt"' in ddl
    assert "request_payload_json JSONB NOT NULL" in ddl
    assert "UNIQUE (tenant_id, user_id, workshop_id, operation, idempotency_key)" in ddl
    assert "ADD COLUMN IF NOT EXISTS active_workshop_version_id" in ddl
    assert "ADD COLUMN IF NOT EXISTS document_sha256 CHAR(64)" in ddl
    assert "ck_ws_version_document_sha256" in ddl
    assert "CHECK (status IN ('open','in_review','concluded','archived'))" in ddl


def test_runtime_postgres_store_rejects_unsafe_schema_identifier() -> None:
    with pytest.raises(ValueError, match="Unsafe Postgres schema identifier"):
        PostgresWorkshopStore(
            dsn="postgresql://example/pantheon",
            schema='agora"; DROP SCHEMA public; --',
        )

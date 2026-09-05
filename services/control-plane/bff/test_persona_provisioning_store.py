"""Contract tests for the durable Persona provisioning coordination store."""
from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier
from typing import Any

import pytest

from services.control_plane.bff.persona_provisioning import (
    MemoryPersonaProvisioningStore,
    PostgresPersonaProvisioningStore,
    ProvisioningConflict,
    ProvisioningLeaseLost,
    ProvisioningRecord,
    make_persona_provisioning_store,
)


TENANT = "tenant-alpha"
IDEMPOTENCY_KEY = "persona-create-alpha"
REQUEST_HASH = "sha256:request-alpha"
NORMALIZED_NAME = "trader alpha"
PERSONA_ID = "persona-alpha"


@pytest.mark.parametrize("environment", ["prod", "production", "staging", "staging-live"])
def test_deployed_environment_refuses_restart_unsafe_memory_store(
    environment: str,
) -> None:
    with pytest.raises(ValueError, match="postgres is required"):
        make_persona_provisioning_store(
            {
                "PANTHEON_ENV": environment,
                "PANTHEON_PERSONA_PROVISIONING_STORE_BACKEND": "memory",
            }
        )


def test_postgres_store_refuses_empty_dsn() -> None:
    with pytest.raises(ValueError, match="DSN or DATABASE_URL is required"):
        make_persona_provisioning_store(
            {"PANTHEON_PERSONA_PROVISIONING_STORE_BACKEND": "postgres"}
        )


def _reserve(
    store: MemoryPersonaProvisioningStore,
    *,
    tenant_id: str = TENANT,
    idempotency_key: str = IDEMPOTENCY_KEY,
    request_hash: str = REQUEST_HASH,
    normalized_name: str = NORMALIZED_NAME,
    persona_id: str = PERSONA_ID,
) -> tuple[ProvisioningRecord, bool]:
    return store.reserve(
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        normalized_name=normalized_name,
        persona_id=persona_id,
        request_payload={"name": "Trader Alpha", "capitalMode": "paper"},
    )


def test_memory_store_recovers_checkpoint_after_coordinator_restart() -> None:
    """A replacement worker resumes durable steps instead of repeating receipts."""
    store = MemoryPersonaProvisioningStore()
    _, created = _reserve(store)
    assert created is True

    first = store.acquire(
        TENANT,
        IDEMPOTENCY_KEY,
        lease_owner="worker-before-restart",
        lease_seconds=60,
    )
    assert first is not None
    first.current_step = "runtime_binding_received"
    first.references = {
        "capital_binding_id": "pcb-alpha",
        "deployment_plan_id": "plan-alpha",
        "runtime_binding_id": "rb-alpha",
    }
    checkpointed = store.checkpoint(first, lease_owner="worker-before-restart")
    store.release(checkpointed, lease_owner="worker-before-restart")

    # The first coordinator has checkpointed and exited.  A replacement process
    # uses only store state to resume from the last owner receipt.
    resumed = store.acquire(
        TENANT,
        IDEMPOTENCY_KEY,
        lease_owner="worker-after-restart",
        lease_seconds=60,
    )
    assert resumed is not None
    assert resumed.attempt_count == 2
    assert resumed.current_step == "runtime_binding_received"
    assert resumed.references == first.references

    resumed.state = "succeeded"
    resumed.current_step = "complete"
    resumed.result = {"persona_id": PERSONA_ID, "state": "paper_running"}
    released = store.release(resumed, lease_owner="worker-after-restart")
    assert released.lease_owner is None
    assert released.lease_expires_at is None

    replay = store.acquire(
        TENANT,
        IDEMPOTENCY_KEY,
        lease_owner="worker-replay",
        lease_seconds=60,
    )
    assert replay is not None
    assert replay.state == "succeeded"
    assert replay.current_step == "complete"
    assert replay.result == {"persona_id": PERSONA_ID, "state": "paper_running"}


def test_memory_store_isolates_keys_names_and_personas_by_tenant() -> None:
    store = MemoryPersonaProvisioningStore()
    first, first_created = _reserve(store, tenant_id="tenant-a")
    second, second_created = _reserve(store, tenant_id="tenant-b")

    assert first_created is True
    assert second_created is True
    assert first.tenant_id == "tenant-a"
    assert second.tenant_id == "tenant-b"
    assert store.get("tenant-a", IDEMPOTENCY_KEY) is not None
    assert store.get("tenant-b", IDEMPOTENCY_KEY) is not None
    assert store.get("tenant-c", IDEMPOTENCY_KEY) is None
    assert store.get_by_persona("tenant-a", PERSONA_ID) is not None
    assert store.get_by_persona("tenant-b", PERSONA_ID) is not None
    assert store.get_by_persona("tenant-c", PERSONA_ID) is None


def test_memory_store_same_key_rejects_changed_request_semantics() -> None:
    store = MemoryPersonaProvisioningStore()
    _reserve(store)

    with pytest.raises(ProvisioningConflict, match="idempotency key"):
        _reserve(
            store,
            request_hash="sha256:different-request",
            normalized_name="different name",
            persona_id="persona-different",
        )


def test_memory_store_same_name_different_key_converges_for_same_request() -> None:
    store = MemoryPersonaProvisioningStore()
    original, original_created = _reserve(store)
    replay, replay_created = _reserve(
        store,
        idempotency_key="persona-create-alpha-retry",
        persona_id="persona-client-generated-but-not-authoritative",
    )

    assert original_created is True
    assert replay_created is False
    assert replay.idempotency_key == original.idempotency_key
    assert replay.persona_id == original.persona_id
    assert replay.request_hash == original.request_hash


def test_memory_store_same_name_different_key_rejects_changed_request() -> None:
    store = MemoryPersonaProvisioningStore()
    _reserve(store)

    with pytest.raises(ProvisioningConflict, match="Persona name"):
        _reserve(
            store,
            idempotency_key="persona-create-alpha-other",
            request_hash="sha256:different-request",
            persona_id="persona-other",
        )


def test_memory_store_concurrent_lease_has_exactly_one_winner() -> None:
    store = MemoryPersonaProvisioningStore()
    _reserve(store)
    worker_count = 12
    barrier = Barrier(worker_count)

    def contend(index: int) -> ProvisioningRecord | None:
        barrier.wait()
        return store.acquire(
            TENANT,
            IDEMPOTENCY_KEY,
            lease_owner=f"worker-{index}",
            lease_seconds=60,
        )

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        results = list(executor.map(contend, range(worker_count)))

    winners = [record for record in results if record is not None]
    assert len(winners) == 1
    assert winners[0].attempt_count == 1
    persisted = store.get(TENANT, IDEMPOTENCY_KEY)
    assert persisted is not None
    assert persisted.lease_owner == winners[0].lease_owner


def test_memory_store_terminal_failure_stays_terminal_when_reacquired() -> None:
    store = MemoryPersonaProvisioningStore()
    _reserve(store)
    first = store.acquire(
        TENANT,
        IDEMPOTENCY_KEY,
        lease_owner="worker-failed",
        lease_seconds=60,
    )
    assert first is not None
    first.state = "failed"
    first.current_step = "runtime_binding_failed"
    first.references = {"runtime_binding_id": "rb-failed"}
    first.error = {"code": "runtime_binding_failed", "retryable": True}
    checkpointed = store.checkpoint(first, lease_owner="worker-failed")
    assert checkpointed.state == "failed"
    store.release(checkpointed, lease_owner="worker-failed")

    retry = store.acquire(
        TENANT,
        IDEMPOTENCY_KEY,
        lease_owner="worker-retry",
        lease_seconds=60,
    )
    assert retry is not None
    assert retry.state == "failed"
    assert retry.attempt_count == 2
    assert retry.current_step == "runtime_binding_failed"
    assert retry.references == {"runtime_binding_id": "rb-failed"}
    assert retry.error == {"code": "runtime_binding_failed", "retryable": True}


def test_memory_store_checkpoint_renews_the_current_lease() -> None:
    store = MemoryPersonaProvisioningStore()
    _reserve(store)
    record = store.acquire(
        TENANT,
        IDEMPOTENCY_KEY,
        lease_owner="worker-renewing",
        lease_seconds=5,
    )
    assert record is not None
    original_expiry = datetime.fromisoformat(record.lease_expires_at.replace("Z", "+00:00"))

    record.current_step = "long_owner_round_trip_readback"
    renewed = store.checkpoint(
        record,
        lease_owner="worker-renewing",
        lease_seconds=120,
    )
    renewed_expiry = datetime.fromisoformat(
        renewed.lease_expires_at.replace("Z", "+00:00")
    )

    assert renewed_expiry > original_expiry
    assert renewed.current_step == "long_owner_round_trip_readback"


def test_memory_store_checkpoint_and_release_require_current_owner() -> None:
    store = MemoryPersonaProvisioningStore()
    _reserve(store)
    record = store.acquire(
        TENANT,
        IDEMPOTENCY_KEY,
        lease_owner="worker-owner",
        lease_seconds=60,
    )
    assert record is not None

    with pytest.raises(ProvisioningLeaseLost):
        store.checkpoint(record, lease_owner="worker-other")
    with pytest.raises(ProvisioningLeaseLost):
        store.release(record, lease_owner="worker-other")


def test_memory_store_checkpoint_rejects_expired_lease() -> None:
    store = MemoryPersonaProvisioningStore()
    _reserve(store)
    expired = store.acquire(
        TENANT,
        IDEMPOTENCY_KEY,
        lease_owner="worker-expired",
        lease_seconds=-1,
    )
    assert expired is not None
    expired.current_step = "must-not-commit-after-expiry"

    with pytest.raises(ProvisioningLeaseLost):
        store.checkpoint(expired, lease_owner="worker-expired")


def test_memory_store_expired_lease_can_be_reacquired_and_fences_old_owner() -> None:
    store = MemoryPersonaProvisioningStore()
    _reserve(store)
    old = store.acquire(
        TENANT,
        IDEMPOTENCY_KEY,
        lease_owner="worker-old",
        lease_seconds=-1,
    )
    assert old is not None

    replacement = store.acquire(
        TENANT,
        IDEMPOTENCY_KEY,
        lease_owner="worker-replacement",
        lease_seconds=60,
    )
    assert replacement is not None
    assert replacement.lease_owner == "worker-replacement"
    assert replacement.attempt_count == 2

    old.current_step = "stale-owner-must-not-commit"
    with pytest.raises(ProvisioningLeaseLost):
        store.checkpoint(old, lease_owner="worker-old")


class _RecordingCursor:
    def __init__(self, backend: "_RecordingConnect") -> None:
        self.backend = backend

    def __enter__(self) -> "_RecordingCursor":
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
        self.backend.executions.append((" ".join(sql.split()), params))

    def fetchone(self) -> Any:
        return self.backend.rows.popleft() if self.backend.rows else None

    def fetchall(self) -> list[Any]:
        rows = list(self.backend.rows)
        self.backend.rows.clear()
        return rows


class _RecordingConnection:
    def __init__(self, backend: "_RecordingConnect") -> None:
        self.backend = backend

    def __enter__(self) -> "_RecordingConnection":
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def cursor(self) -> _RecordingCursor:
        return _RecordingCursor(self.backend)


class _RecordingConnect:
    def __init__(self) -> None:
        self.executions: list[tuple[str, tuple[Any, ...] | None]] = []
        self.rows: deque[Any] = deque()
        self.dsns: list[str] = []

    def __call__(self, dsn: str) -> _RecordingConnection:
        self.dsns.append(dsn)
        return _RecordingConnection(self)


def _postgres_row(
    *,
    state: str = "provisioning",
    current_step: str = "reserved",
    lease_owner: str | None = "worker-postgres",
    attempt_count: int = 1,
) -> tuple[Any, ...]:
    timestamp = datetime(2026, 7, 15, 7, 0, tzinfo=timezone.utc)
    return (
        TENANT,
        IDEMPOTENCY_KEY,
        REQUEST_HASH,
        NORMALIZED_NAME,
        PERSONA_ID,
        {"name": "Trader Alpha"},
        state,
        current_step,
        {"runtime_binding_id": "rb-alpha"},
        None,
        None,
        None,
        timestamp,
        timestamp,
        lease_owner,
        timestamp,
        attempt_count,
    )


def test_postgres_bootstrap_enforces_tenant_scoped_uniqueness() -> None:
    connect = _RecordingConnect()
    store = PostgresPersonaProvisioningStore(
        "postgresql://example/provisioning",
        schema="bff_test",
        connect=connect,
    )

    assert store.table == "bff_test.persona_provisioning"
    create_table = next(sql for sql, _ in connect.executions if "CREATE TABLE" in sql)
    assert '"references" JSONB' in create_table
    assert "PRIMARY KEY (tenant_id, idempotency_key)" in create_table
    assert "UNIQUE (tenant_id, normalized_name)" in create_table
    assert "UNIQUE (tenant_id, persona_id)" in create_table


def test_postgres_lease_sql_is_atomic_and_checkpoint_is_expiry_guarded() -> None:
    connect = _RecordingConnect()
    store = PostgresPersonaProvisioningStore(
        "postgresql://example/provisioning",
        connect=connect,
    )

    connect.rows.append(_postgres_row())
    acquired = store.acquire(
        TENANT,
        IDEMPOTENCY_KEY,
        lease_owner="worker-postgres",
        lease_seconds=45,
    )
    assert acquired is not None
    acquire_sql, acquire_params = connect.executions[-1]
    assert "attempt_count=attempt_count+1" in acquire_sql
    assert "lease_owner IS NULL OR lease_owner=%s OR lease_expires_at <= now()" in acquire_sql
    assert acquire_params == (
        "worker-postgres",
        45,
        TENANT,
        IDEMPOTENCY_KEY,
        "worker-postgres",
    )

    acquired.current_step = "runtime_binding_received"
    connect.rows.append(
        _postgres_row(current_step="runtime_binding_received", lease_owner="worker-postgres")
    )
    saved = store.checkpoint(
        acquired,
        lease_owner="worker-postgres",
        lease_seconds=45,
    )
    assert saved.current_step == "runtime_binding_received"
    checkpoint_sql, checkpoint_params = connect.executions[-1]
    assert "lease_owner=%s" in checkpoint_sql
    assert "lease_expires_at > now()" in checkpoint_sql
    assert "lease_expires_at=now() + (%s * interval '1 second')" in checkpoint_sql
    assert checkpoint_params[-4] == 45
    assert checkpoint_params[-3:] == (TENANT, IDEMPOTENCY_KEY, "worker-postgres")


def test_memory_store_list_by_tenant_and_list_all() -> None:
    store = MemoryPersonaProvisioningStore()
    _reserve(store, tenant_id="tenant-1", idempotency_key="key-1", normalized_name="name 1", persona_id="p-1")
    _reserve(store, tenant_id="tenant-1", idempotency_key="key-2", normalized_name="name 2", persona_id="p-2")
    _reserve(store, tenant_id="tenant-2", idempotency_key="key-3", normalized_name="name 3", persona_id="p-3")

    tenant1_records = store.list_by_tenant("tenant-1")
    assert len(tenant1_records) == 2
    assert [r.persona_id for r in tenant1_records] == ["p-1", "p-2"]

    tenant2_records = store.list_by_tenant("tenant-2")
    assert len(tenant2_records) == 1
    assert tenant2_records[0].persona_id == "p-3"

    all_records = store.list_all()
    assert len(all_records) == 3


def test_postgres_store_list_by_tenant_and_list_all() -> None:
    connect = _RecordingConnect()
    store = PostgresPersonaProvisioningStore(
        "postgresql://example/provisioning",
        connect=connect,
    )
    connect.rows.append(_postgres_row(state="succeeded"))
    records = store.list_by_tenant(TENANT)
    assert len(records) == 1
    assert records[0].persona_id == PERSONA_ID
    list_sql, list_params = connect.executions[-1]
    assert "WHERE tenant_id=%s" in list_sql
    assert "ORDER BY created_at ASC, persona_id ASC" in list_sql
    assert list_params == (TENANT,)

    connect.rows.append(_postgres_row(state="succeeded"))
    all_records = store.list_all()
    assert len(all_records) == 1
    all_sql, _ = connect.executions[-1]
    assert "ORDER BY created_at ASC, persona_id ASC" in all_sql

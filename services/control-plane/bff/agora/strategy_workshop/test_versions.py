from __future__ import annotations

import os
import uuid

import pytest

from .store import (
    MemoryWorkshopStore,
    PostgresWorkshopStore,
    WorkshopVersionProjectionConflict,
)


WORKSHOP_ID = "ws-legacy-version-projection"
STRATEGY_ID = "strategy-legacy-version-projection"
REGISTRY_ID = "registry-legacy-version-projection"
DIGEST = "a" * 64
CREATED_AT = "2026-07-22T12:00:00Z"


def _legacy_session() -> dict[str, str]:
    return {
        "workshop_id": WORKSHOP_ID,
        "tenant_id": "tenant-version-projection",
        "user_id": "user-version-projection",
        "strategy_id": STRATEGY_ID,
        "active_strategy_spec_registry_id": REGISTRY_ID,
        "status": "open",
        "created_at": CREATED_AT,
        "updated_at": CREATED_AT,
    }


def test_memory_legacy_backfill_is_deterministic_and_etag_stable() -> None:
    store = MemoryWorkshopStore()
    before = store.create_session(_legacy_session())

    first = store.ensure_current_version_link(
        workshop_id=WORKSHOP_ID,
        strategy_id=STRATEGY_ID,
        strategy_spec_registry_id=REGISTRY_ID,
        document_sha256=DIGEST,
    )
    replay = store.ensure_current_version_link(
        workshop_id=WORKSHOP_ID,
        strategy_id=STRATEGY_ID,
        strategy_spec_registry_id=REGISTRY_ID,
        document_sha256=DIGEST,
    )

    assert replay == first
    assert first["workshop_version_id"].startswith("wsv-legacy-")
    assert first["parent_workshop_version_id"] is None
    assert first["sequence_no"] == 1
    assert first["document_sha256"] == DIGEST
    assert first["created_by"] == before["user_id"]
    assert first["created_at"] == before["created_at"]
    assert store.list_version_links(WORKSHOP_ID) == [first]

    after = store.get_session(WORKSHOP_ID)
    assert after is not None
    assert after["selected_version_id"] == first["workshop_version_id"]
    assert after["active_workshop_version_id"] == first["workshop_version_id"]
    for stable_field in (
        "workshop_id",
        "tenant_id",
        "user_id",
        "strategy_id",
        "active_strategy_spec_registry_id",
        "status",
        "lock_version",
        "created_at",
        "updated_at",
    ):
        assert after[stable_field] == before[stable_field]


def test_memory_version_digest_is_write_once() -> None:
    store = MemoryWorkshopStore()
    store.create_session(_legacy_session())
    link = store.ensure_current_version_link(
        workshop_id=WORKSHOP_ID,
        strategy_id=STRATEGY_ID,
        strategy_spec_registry_id=REGISTRY_ID,
        document_sha256=DIGEST,
    )

    with pytest.raises(
        WorkshopVersionProjectionConflict,
        match="immutable version",
    ):
        store.ensure_version_link_digest(
            workshop_id=WORKSHOP_ID,
            workshop_version_id=link["workshop_version_id"],
            strategy_id=STRATEGY_ID,
            document_sha256="b" * 64,
        )


def test_postgres_versions_and_selection_survive_store_restart() -> None:
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is not set")
    schema = f"agora_ws_ops_{uuid.uuid4().hex[:12]}"
    workshop_id = f"ws-{uuid.uuid4().hex}"
    base_registry_id = f"registry-{uuid.uuid4().hex}"
    next_registry_id = f"registry-{uuid.uuid4().hex}"
    base_digest = "c" * 64
    next_digest = "d" * 64

    first_store = PostgresWorkshopStore(dsn=dsn, schema=schema)
    first_store.create_session(
        {
            "workshop_id": workshop_id,
            "tenant_id": "tenant-restart",
            "user_id": "user-restart",
            "strategy_id": "strategy-restart",
            "active_strategy_spec_registry_id": base_registry_id,
            "status": "open",
        }
    )
    base = first_store.ensure_current_version_link(
        workshop_id=workshop_id,
        strategy_id="strategy-restart",
        strategy_spec_registry_id=base_registry_id,
        document_sha256=base_digest,
    )
    admitted = first_store.admit_command(
        workshop_id=workshop_id,
        tenant_id="tenant-restart",
        user_id="user-restart",
        operation="create_version",
        idempotency_key="restart-version-create",
        request_hash="e" * 64,
        expected_lock_version=1,
    )
    assert admitted["outcome"] == "admitted"
    completed = first_store.complete_command(
        workshop_id=workshop_id,
        tenant_id="tenant-restart",
        user_id="user-restart",
        operation="create_version",
        idempotency_key="restart-version-create",
        request_hash="e" * 64,
        result={"strategy_spec_registry_id": next_registry_id},
        version_link={
            "workshop_version_id": "wsv-restart-next",
            "strategy_id": "strategy-restart",
            "strategy_spec_registry_id": next_registry_id,
            "parent_workshop_version_id": base["workshop_version_id"],
            "sequence_no": 2,
            "document_sha256": next_digest,
            "created_by": "user-restart",
        },
        session_updates={
            "active_strategy_spec_registry_id": next_registry_id,
            "active_workshop_version_id": "wsv-restart-next",
        },
    )
    assert completed["outcome"] == "completed"

    restarted_store = PostgresWorkshopStore(dsn=dsn, schema=schema)
    assert [
        row["document_sha256"]
        for row in restarted_store.list_version_links(workshop_id)
    ] == [base_digest, next_digest]
    restarted_session = restarted_store.get_session(workshop_id)
    assert restarted_session is not None
    assert restarted_session["selected_version_id"] == "wsv-restart-next"
    assert restarted_session["active_workshop_version_id"] == "wsv-restart-next"
    assert restarted_session["active_strategy_spec_registry_id"] == next_registry_id

"""Tests for PostgresTwelveLoopStore and migration execution.

Verifies schema creation, idempotency, and async store operations against
PostgreSQL under task LOOP-TRUTH-001.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import os
import pytest

from services.control_plane.bff.management_read_models.twelve_loop_projector import (
    CanonicalLoopReceipt,
    LoopObservation,
    TwelveLoopTruthProjector,
)
from services.control_plane.bff.migrations.twelve_loop_truth import (
    PostgresTwelveLoopStore,
    build_twelve_loop_store,
)

POSTGRES_TEST_DSN = os.environ.get(
    "REVIEW_TEST_DSN",
    os.environ.get("TEST_DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:15432/pantheon"),
)


@pytest.mark.anyio
async def test_postgres_migration_and_store_operations() -> None:
    store = PostgresTwelveLoopStore(POSTGRES_TEST_DSN)

    # 1. Apply migration
    await store.apply_migration()

    # 2. Record receipt
    now = datetime.now(timezone.utc)
    receipt = CanonicalLoopReceipt(
        receipt_id="pg-rcpt-001",
        receipt_type="terminal",
        loop_id=7,
        correlation_id="corr-pg-01",
        release_id="rel-pg-01",
        owner="consultation provider",
        provenance="live",
        status="completed",
        observed_at=now,
        degradation_reason=None,
        causation_id="cause-pg-01",
        payload={"result": "approved"},
    )
    await store.record_receipt_async(receipt)

    # 3. Idempotent re-record (must not fail)
    await store.record_receipt_async(receipt)

    # 4. Upsert observation
    obs = LoopObservation(
        release_id="rel-pg-01",
        correlation_id="corr-pg-01",
        loop_id=7,
        owner="consultation provider",
        terminal_id="pg-rcpt-001",
        terminal_status="completed",
        status="open",
        freshness_status="fresh",
        provenance="live",
        observed_at=now,
        receipt_ids=["pg-rcpt-001"],
    )
    await store.upsert_observation_async(obs)

    # 5. Update observation on conflict (e.g. next_consumer added -> complete)
    obs.next_consumer_receipt_id = "pg-rcpt-next-001"
    obs.status = "complete"
    await store.upsert_observation_async(obs)


def test_postgres_store_supports_receipt_reload_interface_check() -> None:
    """Verify PostgresTwelveLoopStore implements list_receipts interface without NotImplementedError."""
    store = PostgresTwelveLoopStore("postgresql://unused-for-this-interface-check")
    receipts = store.list_receipts(release_id="review-release")
    assert receipts == []


def test_postgres_store_persistence_and_fresh_projector_reload() -> None:
    """Acceptance: Complete persisted ingest, read, restart, and rebuild wiring with real PostgreSQL."""
    store = PostgresTwelveLoopStore(POSTGRES_TEST_DSN)
    store.apply_migration_sync()

    unique_suffix = f"{int(datetime.now(timezone.utc).timestamp())}_{os.getpid()}"
    release_id = f"rel-pg-reload-{unique_suffix}"
    correlation_id = f"corr-pg-reload-{unique_suffix}"

    now = datetime.now(timezone.utc)
    stimulus = CanonicalLoopReceipt(
        receipt_id=f"rcpt-pg-stim-{unique_suffix}",
        receipt_type="stimulus",
        loop_id=1,
        correlation_id=correlation_id,
        release_id=release_id,
        owner="source-ingest connector",
        provenance="live",
        observed_at=now,
    )
    terminal = CanonicalLoopReceipt(
        receipt_id=f"rcpt-pg-term-{unique_suffix}",
        receipt_type="terminal",
        loop_id=1,
        correlation_id=correlation_id,
        release_id=release_id,
        owner="source-ingest connector",
        provenance="live",
        status="completed",
        observed_at=now,
    )
    next_consumer = CanonicalLoopReceipt(
        receipt_id=f"rcpt-pg-next-{unique_suffix}",
        receipt_type="next_consumer",
        loop_id=1,
        correlation_id=correlation_id,
        release_id=release_id,
        owner="distillation connector",
        provenance="live",
        status="completed",
        observed_at=now,
    )

    # Ingest through projector wired to postgres store
    p1 = TwelveLoopTruthProjector(store=store)
    p1.ingest_receipts([stimulus, terminal, next_consumer])

    obs1 = p1.get_observation(release_id, correlation_id, 1)
    assert obs1 is not None
    assert obs1.status == "complete"

    # Verify rows persisted directly in Postgres
    receipts_in_db = store.list_receipts(release_id=release_id)
    assert len(receipts_in_db) == 3

    obs_in_db = store.get_observation(release_id, correlation_id, 1)
    assert obs_in_db is not None
    assert obs_in_db.status == "complete"
    assert obs_in_db.terminal_id == f"rcpt-pg-term-{unique_suffix}"
    assert obs_in_db.next_consumer_receipt_id == f"rcpt-pg-next-{unique_suffix}"

    # Restart scenario: fresh projector instance reloads from Postgres
    p2 = TwelveLoopTruthProjector(store=store)
    obs2 = p2.get_observation(release_id, correlation_id, 1)
    assert obs2 is not None
    assert obs2.status == "complete"
    assert obs2.to_dict() == obs1.to_dict()

    # Rebuild from stored receipts matches incremental state
    p2.rebuild()
    obs_rebuilt = p2.get_observation(release_id, correlation_id, 1)
    assert obs_rebuilt is not None
    assert obs_rebuilt.to_dict() == obs1.to_dict()

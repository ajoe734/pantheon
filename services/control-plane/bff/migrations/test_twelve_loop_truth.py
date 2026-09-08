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

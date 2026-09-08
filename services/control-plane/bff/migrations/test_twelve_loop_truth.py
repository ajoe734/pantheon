"""Tests for PostgresTwelveLoopStore and migration execution.

Verifies schema creation, idempotency, and async store operations against
PostgreSQL under task LOOP-TRUTH-001.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
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


from unittest.mock import MagicMock, patch


def test_postgres_store_supports_receipt_reload_interface_check() -> None:
    """Verify PostgresTwelveLoopStore implements list_receipts interface without NotImplementedError using mocked connection."""
    store = PostgresTwelveLoopStore("postgresql://mock-host:5432/mock_db")
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = []
    mock_conn = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    with patch.object(store, "_connect", return_value=mock_conn):
        receipts = store.list_receipts(release_id="review-release")
        assert receipts == []


def test_postgres_store_unreachable_dsn_raises() -> None:
    """P1: Configured unreachable PostgreSQL DSN must raise rather than silently no-oping."""
    store = PostgresTwelveLoopStore("postgresql://invalid:invalid@127.0.0.1:59999/dummy_receipts")
    with pytest.raises(Exception):
        store.list_receipts(release_id="review-release")


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


def test_postgres_interleaving_projectors_fence_stale_backfill() -> None:
    """P1: Two projectors on one PostgreSQL store: Projector A ingests live truth,

    Projector B ingests 120-second-older backfill failed terminal.
    Durable observation remains complete/live; fresh rebuild returns complete/live.
    """
    store = PostgresTwelveLoopStore(POSTGRES_TEST_DSN)
    store.apply_migration_sync()

    unique_suffix = f"interleave_{int(datetime.now(timezone.utc).timestamp())}_{os.getpid()}"
    release_id = f"rel-pg-interleave-{unique_suffix}"
    correlation_id = f"corr-pg-interleave-{unique_suffix}"

    now = datetime.now(timezone.utc)
    stimulus = CanonicalLoopReceipt(
        receipt_id=f"rcpt-stim-{unique_suffix}",
        receipt_type="stimulus",
        loop_id=1,
        correlation_id=correlation_id,
        release_id=release_id,
        owner="source-ingest connector",
        provenance="live",
        observed_at=now,
    )
    terminal = CanonicalLoopReceipt(
        receipt_id=f"rcpt-term-{unique_suffix}",
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
        receipt_id=f"rcpt-next-{unique_suffix}",
        receipt_type="next_consumer",
        loop_id=1,
        correlation_id=correlation_id,
        release_id=release_id,
        owner="distillation connector",
        provenance="live",
        status="completed",
        observed_at=now,
    )

    # Projector A ingests live stimulus, completed terminal, accepted consumer
    p_a = TwelveLoopTruthProjector(store=store)
    p_a.ingest_receipts([stimulus, terminal, next_consumer])

    obs_a = p_a.get_observation(release_id, correlation_id, 1)
    assert obs_a is not None
    assert obs_a.status == "complete"
    assert obs_a.provenance == "live"

    # Projector B is a separate instance on the same store.
    # B ingests 120-second-older backfill failed terminal
    p_b = TwelveLoopTruthProjector(store=store, auto_load=False)
    older_backfill = CanonicalLoopReceipt(
        receipt_id=f"rcpt-backfill-term-{unique_suffix}",
        receipt_type="terminal",
        loop_id=1,
        correlation_id=correlation_id,
        release_id=release_id,
        owner="source-ingest connector",
        provenance="backfill",
        status="failed",
        observed_at=now - timedelta(seconds=120),
    )
    obs_b = p_b.ingest_receipt(older_backfill)

    # Ingestion through B must NOT overwrite durable live truth with backfill failed
    assert obs_b.status == "complete"
    assert obs_b.provenance == "live"

    # Durable truth in PostgreSQL remains complete/live
    obs_db = store.get_observation(release_id, correlation_id, 1)
    assert obs_db is not None
    assert obs_db.status == "complete"
    assert obs_db.provenance == "live"

    # Fresh rebuild produces complete/live
    p_c = TwelveLoopTruthProjector(store=store)
    p_c.rebuild()
    obs_c = p_c.get_observation(release_id, correlation_id, 1)
    assert obs_c is not None
    assert obs_c.status == "complete"
    assert obs_c.provenance == "live"


def test_postgres_rejects_cross_key_duplicate_receipt_id() -> None:
    """P1: Same terminal ID first failed under c1 then completed under c2.

    Must reject conflicting identity and prevent fresh reload divergence.
    """
    store = PostgresTwelveLoopStore(POSTGRES_TEST_DSN)
    store.apply_migration_sync()

    unique_suffix = f"crosskey_{int(datetime.now(timezone.utc).timestamp())}_{os.getpid()}"
    release_id = f"rel-pg-crosskey-{unique_suffix}"
    shared_term_id = f"rcpt-term-dup-{unique_suffix}"

    now = datetime.now(timezone.utc)
    term_c1 = CanonicalLoopReceipt(
        receipt_id=shared_term_id,
        receipt_type="terminal",
        loop_id=1,
        correlation_id="c1",
        release_id=release_id,
        owner="source-ingest connector",
        provenance="live",
        status="failed",
        observed_at=now,
    )
    term_c2 = CanonicalLoopReceipt(
        receipt_id=shared_term_id,
        receipt_type="terminal",
        loop_id=1,
        correlation_id="c2",
        release_id=release_id,
        owner="source-ingest connector",
        provenance="live",
        status="completed",
        observed_at=now,
    )

    p1 = TwelveLoopTruthProjector(store=store)
    p1.ingest_receipt(term_c1)
    assert p1.get_observation(release_id, "c1", 1).status == "failed"

    # Ingesting the same receipt ID under c2 must be rejected
    with pytest.raises(ValueError, match="Conflicting receipt identity"):
        p1.ingest_receipt(term_c2)

    # Even a fresh projector instance querying Postgres rejects the conflicting identity
    p2 = TwelveLoopTruthProjector(store=store, auto_load=False)
    with pytest.raises(ValueError, match="Conflicting receipt identity"):
        p2.ingest_receipt(term_c2)


def test_postgres_partial_write_retry() -> None:
    """P1: Partial-write retry: receipt was recorded in store but observation was not written;

    subsequent ingest_receipt completes the write successfully.
    """
    store = PostgresTwelveLoopStore(POSTGRES_TEST_DSN)
    store.apply_migration_sync()

    unique_suffix = f"partial_{int(datetime.now(timezone.utc).timestamp())}_{os.getpid()}"
    release_id = f"rel-pg-partial-{unique_suffix}"
    correlation_id = f"corr-pg-partial-{unique_suffix}"

    now = datetime.now(timezone.utc)
    stimulus = CanonicalLoopReceipt(
        receipt_id=f"rcpt-stim-partial-{unique_suffix}",
        receipt_type="stimulus",
        loop_id=1,
        correlation_id=correlation_id,
        release_id=release_id,
        owner="source-ingest connector",
        provenance="live",
        observed_at=now,
    )

    # Simulate partial write: record receipt directly in Postgres store without upserting observation
    store.record_receipt(stimulus)
    assert store.get_observation(release_id, correlation_id, 1) is None

    # Now ingest through projector: retry should detect recorded receipt, compute observation, and upsert
    p = TwelveLoopTruthProjector(store=store)
    obs = p.ingest_receipt(stimulus)
    assert obs.status == "open"
    assert obs.stimulus_id == stimulus.receipt_id

    # Observation is now durable in Postgres
    obs_db = store.get_observation(release_id, correlation_id, 1)
    assert obs_db is not None
    assert obs_db.status == "open"
    assert obs_db.stimulus_id == stimulus.receipt_id


def test_postgres_equal_timestamp_interleaving_cannot_restore_complete() -> None:
    """P1 regression: interleaved terminal persistence at equal timestamp cannot be overwritten by stale complete in PostgreSQL."""
    from uuid import uuid4

    store = PostgresTwelveLoopStore(POSTGRES_TEST_DSN)
    store.apply_migration_sync()

    prefix = f"pg_eq_{uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)

    def make(name: str, kind: str, status: str = "", offset: int = 0, cause: Optional[str] = None, corr: str = "c") -> CanonicalLoopReceipt:
        return CanonicalLoopReceipt(
            receipt_id=f"{prefix}_{name}",
            receipt_type=kind,
            release_id=prefix,
            correlation_id=corr,
            loop_id=1,
            owner="review",
            provenance="live",
            status=status,
            observed_at=now + timedelta(seconds=offset),
            causation_id=f"{prefix}_{cause}" if cause else None,
        )

    p1 = TwelveLoopTruthProjector(store, auto_load=False)
    p2 = TwelveLoopTruthProjector(store, auto_load=False)
    p1.ingest_receipts([make("s", "stimulus"), make("t", "terminal", "completed", cause="s")])
    original = store.upsert_observation

    def interleave(obs: LoopObservation) -> None:
        store.upsert_observation = original
        p2.ingest_receipt(make("z", "terminal", "failed", cause="s"))
        assert store.get_observation(prefix, "c", 1).status == "failed"
        original(obs)

    store.upsert_observation = interleave
    p1.ingest_receipt(make("n", "next_consumer", "accepted", cause="t"))
    assert store.get_observation(prefix, "c", 1).status == "failed"


def test_postgres_late_stimulus_incremental_equals_rebuild() -> None:
    """P1 regression: late stimulus invalidates old chain and incremental reduction equals rebuild in PostgreSQL."""
    from uuid import uuid4

    store = PostgresTwelveLoopStore(POSTGRES_TEST_DSN)
    store.apply_migration_sync()

    prefix = f"pg_late_{uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)

    def make(name: str, kind: str, status: str = "", offset: int = 0, cause: Optional[str] = None, corr: str = "c") -> CanonicalLoopReceipt:
        return CanonicalLoopReceipt(
            receipt_id=f"{prefix}_{name}",
            receipt_type=kind,
            release_id=prefix,
            correlation_id=corr,
            loop_id=1,
            owner="review",
            provenance="live",
            status=status,
            observed_at=now + timedelta(seconds=offset),
            causation_id=f"{prefix}_{cause}" if cause else None,
        )

    p = TwelveLoopTruthProjector(store, auto_load=False)
    p.ingest_receipts([
        make("s", "stimulus", offset=-30),
        make("t", "terminal", "completed", offset=-20, cause="s"),
        make("n", "next_consumer", "accepted", offset=-10, cause="t"),
    ])
    incremental = p.ingest_receipt(make("s2", "stimulus", offset=-15)).status
    rebuilt = p.rebuild()[0].status
    assert incremental == rebuilt == "open"


def test_postgres_duplicate_race_cannot_project_unpersisted_cross_key_receipt() -> None:
    """P1 regression: concurrent duplicate insert after get_receipt cannot project unpersisted content under another correlation in PostgreSQL."""
    from uuid import uuid4

    store = PostgresTwelveLoopStore(POSTGRES_TEST_DSN)
    store.apply_migration_sync()

    prefix = f"pg_dup_{uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)

    def make(name: str, kind: str, status: str = "", offset: int = 0, cause: Optional[str] = None, corr: str = "c") -> CanonicalLoopReceipt:
        return CanonicalLoopReceipt(
            receipt_id=f"{prefix}_{name}",
            receipt_type=kind,
            release_id=prefix,
            correlation_id=corr,
            loop_id=1,
            owner="review",
            provenance="live",
            status=status,
            observed_at=now + timedelta(seconds=offset),
            causation_id=f"{prefix}_{cause}" if cause else None,
        )

    p = TwelveLoopTruthProjector(store, auto_load=False)
    original = store.record_receipt

    def interleave(receipt: CanonicalLoopReceipt) -> None:
        store.record_receipt = original
        original(make("t", "terminal", "failed", corr="other"))
        original(receipt)

    store.record_receipt = interleave
    with pytest.raises(ValueError, match="Conflicting receipt identity"):
        p.ingest_receipt(make("t", "terminal", "completed"))


@pytest.mark.parametrize("provenance", ["replay", "backfill"])
def test_postgres_late_lower_provenance_stimulus_keeps_incremental_rebuild_equal(provenance: str) -> None:
    """P1 regression: late lower-provenance stimulus cannot invalidate live terminal chain in PostgreSQL store."""
    from uuid import uuid4
    store = PostgresTwelveLoopStore(POSTGRES_TEST_DSN)
    store.apply_migration_sync()

    prefix = "pg-late-" + uuid4().hex
    now = datetime.now(timezone.utc)

    def receipt(name: str, kind: str, status: str = "", r_prov: str = "live", offset: int = 0, cause: Optional[str] = None) -> CanonicalLoopReceipt:
        return CanonicalLoopReceipt(
            receipt_id=prefix + name,
            receipt_type=kind,
            loop_id=1,
            correlation_id=prefix,
            release_id=prefix,
            owner="review",
            provenance=r_prov,
            status=status,
            observed_at=now + timedelta(seconds=offset),
            causation_id=prefix + cause if cause else None,
        )

    projector = TwelveLoopTruthProjector(store, auto_load=False)
    projector.ingest_receipts([
        receipt("t", "terminal", "completed", offset=-20, cause="original-stimulus"),
        receipt("n", "next_consumer", "accepted", offset=-10, cause="t"),
    ])
    assert projector.get_observation(prefix, prefix, 1).status == "complete"
    incremental = projector.ingest_receipt(receipt("late-stimulus", "stimulus", r_prov=provenance, offset=-30))
    rebuilt = projector.rebuild()[0]
    durable = store.get_observation(prefix, prefix, 1)
    assert incremental.to_dict() == rebuilt.to_dict() == durable.to_dict()
    assert incremental.status == "complete"
    assert incremental.provenance == "live"
    assert incremental.terminal_id == prefix + "t"
    assert incremental.next_consumer_receipt_id == prefix + "n"
    assert set(incremental.receipt_ids) == {prefix + "t", prefix + "n", prefix + "late-stimulus"}


def test_postgres_receipt_set_containment_and_provenance_fencing() -> None:
    """P1 regression: PostgresTwelveLoopStore enforces receipt_ids containment and provenance fencing in SQL."""
    from uuid import uuid4
    store = PostgresTwelveLoopStore(POSTGRES_TEST_DSN)
    store.apply_migration_sync()

    prefix = "pg-fence-" + uuid4().hex
    now = datetime.now(timezone.utc)
    obs_base = LoopObservation(
        release_id=prefix,
        correlation_id=prefix,
        loop_id=1,
        owner="review",
        status="complete",
        freshness_status="fresh",
        provenance="live",
        observed_at=now,
        receipt_ids=[f"{prefix}-1", f"{prefix}-2"],
    )
    store.upsert_observation(obs_base)
    assert store.get_observation(prefix, prefix, 1).status == "complete"

    # 1. Non-superset update with live provenance rejected by @>
    obs_stale = LoopObservation(
        release_id=prefix,
        correlation_id=prefix,
        loop_id=1,
        owner="review",
        status="failed",
        freshness_status="fresh",
        provenance="live",
        observed_at=now + timedelta(seconds=10),
        receipt_ids=[f"{prefix}-3"],
    )
    store.upsert_observation(obs_stale)
    assert store.get_observation(prefix, prefix, 1).status == "complete"

    # 2. Superset update with lower provenance rejected by >= provenance
    obs_backfill = LoopObservation(
        release_id=prefix,
        correlation_id=prefix,
        loop_id=1,
        owner="review",
        status="failed",
        freshness_status="fresh",
        provenance="backfill",
        observed_at=now + timedelta(seconds=10),
        receipt_ids=[f"{prefix}-1", f"{prefix}-2", f"{prefix}-3"],
    )
    store.upsert_observation(obs_backfill)
    assert store.get_observation(prefix, prefix, 1).provenance == "live"
    assert store.get_observation(prefix, prefix, 1).status == "complete"

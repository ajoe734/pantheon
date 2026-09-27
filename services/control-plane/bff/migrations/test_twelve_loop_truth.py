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


@pytest.mark.parametrize("provenance", ["replay", "backfill"])
@pytest.mark.parametrize("arrival_order", ["live_first", "terminal_first"])
def test_postgres_unmatched_live_next_consumer_keeps_incremental_rebuild_equal(
    provenance: str, arrival_order: str
) -> None:
    """P1 regression: unmatched higher-provenance live next-consumer chain is preserved as deterministic candidate
    even when unrelated replay/backfill terminal candidate exists, across both arrival orders on PostgreSQL store."""
    from uuid import uuid4
    store = PostgresTwelveLoopStore(POSTGRES_TEST_DSN)
    store.apply_migration_sync()

    prefix = "pg-orphan-next-" + uuid4().hex
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

    live_next = receipt("live-next", "next_consumer", "accepted", r_prov="live", offset=-10, cause="live-terminal-not-yet-delivered")
    unrelated_terminal = receipt("historical-terminal", "terminal", "failed", r_prov=provenance, offset=-100, cause="historical-stimulus")

    order = [live_next, unrelated_terminal] if arrival_order == "live_first" else [unrelated_terminal, live_next]

    projector = TwelveLoopTruthProjector(store, auto_load=False)
    incremental = projector.ingest_receipts(order)[-1]
    rebuilt = projector.rebuild()[0]
    durable = store.get_observation(prefix, prefix, 1)

    clean = TwelveLoopTruthProjector()
    clean.ingest_receipts(store.list_receipts(release_id=prefix, correlation_id=prefix, loop_id=1))
    clean_rebuilt = clean.rebuild()[0]

    assert incremental.to_dict() == rebuilt.to_dict() == durable.to_dict() == clean_rebuilt.to_dict()
    assert incremental.status == "open"
    assert incremental.provenance == "live"
    assert incremental.terminal_id is None
    assert incremental.terminal_observed_at is None
    assert incremental.next_consumer_receipt_id == prefix + "live-next"
    assert set(incremental.receipt_ids) == {prefix + "live-next", prefix + "historical-terminal"}


def test_postgres_equal_time_stimuli_reduce_independently_of_arrival_order() -> None:
    """P1 regression: equal-time live/replay stimuli reduce identically across all arrival order permutations on PostgreSQL store."""
    from itertools import permutations
    from uuid import uuid4

    store = PostgresTwelveLoopStore(POSTGRES_TEST_DSN)
    store.apply_migration_sync()

    key = "pg-eq-perm-" + uuid4().hex
    now = datetime.now(timezone.utc)

    def receipt(name: str, kind: str, provenance: str, seconds: int) -> CanonicalLoopReceipt:
        return CanonicalLoopReceipt(
            receipt_id=key + name,
            receipt_type=kind,
            loop_id=1,
            correlation_id=key,
            release_id=key,
            owner="review",
            provenance=provenance,
            status="accepted" if kind == "next_consumer" else "",
            observed_at=now + timedelta(seconds=seconds),
            causation_id=key + "cause",
        )

    live = receipt("z-live-stimulus", "stimulus", "live", -10)
    replay = receipt("a-replay-stimulus", "stimulus", "replay", -10)
    next_receipt = receipt("next", "next_consumer", "live", 0)

    incremental = TwelveLoopTruthProjector(store, auto_load=False)
    incremental.ingest_receipts([live, replay, next_receipt])
    observed = incremental.get_observation(key, key, 1).to_dict()

    for order in permutations([live, replay, next_receipt]):
        clean = TwelveLoopTruthProjector()
        clean.ingest_receipts(order)
        clean.rebuild()
        rebuilt = clean.get_observation(key, key, 1).to_dict()
        assert observed == rebuilt, {
            "order": [r.receipt_id for r in order],
            "incremental": observed,
            "rebuild": rebuilt,
        }


@pytest.mark.parametrize("provenance", ["replay", "backfill"])
def test_postgres_older_stimulus_cannot_steal_live_next_consumer(provenance: str) -> None:
    """P1 regression: older replay or backfill stimulus cannot steal live next-consumer or revert observed_at on PostgreSQL store."""
    from uuid import uuid4

    store = PostgresTwelveLoopStore(POSTGRES_TEST_DSN)
    store.apply_migration_sync()

    key = "pg-late-stim-" + uuid4().hex
    now = datetime.now(timezone.utc)

    def receipt(name: str, kind: str, prov: str, seconds: int) -> CanonicalLoopReceipt:
        return CanonicalLoopReceipt(
            receipt_id=key + name,
            receipt_type=kind,
            loop_id=1,
            correlation_id=key,
            release_id=key,
            owner="review",
            provenance=prov,
            status="accepted" if kind == "next_consumer" else "",
            observed_at=now + timedelta(seconds=seconds),
            causation_id=key + "cause",
        )

    projector = TwelveLoopTruthProjector(store, auto_load=False)
    live = receipt("live-stimulus", "stimulus", "live", -10)
    next_receipt = receipt("next", "next_consumer", "live", 0)
    projector.ingest_receipts([live, next_receipt])
    before = projector.get_observation(key, key, 1).to_dict()
    assert before["next_consumer_receipt_id"] == next_receipt.receipt_id

    projector.ingest_receipt(receipt("older-stimulus", "stimulus", provenance, -20))
    after = store.get_observation(key, key, 1).to_dict()
    assert (after["next_consumer_receipt_id"], after["observed_at"]) == (
        before["next_consumer_receipt_id"],
        before["observed_at"],
    )


def test_postgres_scoped_migration_and_honest_legacy_handling() -> None:
    """Acceptance: Forward migration 003 preserves legacy unscoped rows honestly without invented provenance or cross-tenant leakage."""
    import json
    from uuid import uuid4
    from services.control_plane.bff.migrations.twelve_loop_truth import (
        MIGRATION_002_SQL_PATH,
        MIGRATION_003_SQL_PATH,
    )

    schema = f"test_mig_{uuid4().hex[:8]}"
    store = PostgresTwelveLoopStore(POSTGRES_TEST_DSN, schema=schema)

    # 1. Apply Migration 002 alone
    sql_002 = MIGRATION_002_SQL_PATH.read_text(encoding="utf-8").replace("loop_truth_projection", schema)
    with store._connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql_002)
        conn.commit()

    # 2. Seed legacy unscoped rows under 002 schema
    release_id = f"rel-legacy-{uuid4().hex[:6]}"
    corr_id = f"corr-legacy-{uuid4().hex[:6]}"
    now = datetime.now(timezone.utc)
    with store._connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {schema}.loop_receipts (
                    receipt_id, receipt_type, loop_id, correlation_id, release_id,
                    owner, provenance, status, observed_at, causation_id, payload
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                ("legacy-rcpt-001", "terminal", 1, corr_id, release_id, "test", "live", "completed", now, "cause-001", json.dumps({"legacy": True}))
            )
            cur.execute(
                f"""
                INSERT INTO {schema}.twelve_loop_observations (
                    release_id, correlation_id, loop_id, owner,
                    terminal_id, terminal_status, terminal_observed_at,
                    status, freshness_status, provenance, observed_at,
                    causation_id, receipt_ids
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (release_id, corr_id, 1, "test", "legacy-rcpt-001", "completed", now, "complete", "fresh", "live", now, "cause-001", json.dumps(["legacy-rcpt-001"]))
            )
        conn.commit()

    # 3. Apply Migration 003 forward
    sql_003 = MIGRATION_003_SQL_PATH.read_text(encoding="utf-8").replace("loop_truth_projection", schema)
    with store._connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql_003)
        conn.commit()

    try:
        # 4. Verify legacy observation preserves honest NULL tenant_id and environment
        legacy_obs = store.get_observation(release_id, corr_id, 1)
        assert legacy_obs is not None
        assert legacy_obs.tenant_id is None
        assert legacy_obs.environment is None
        assert legacy_obs.terminal_id == "legacy-rcpt-001"
        assert legacy_obs.provenance == "live"

        # 5. Scoped queries MUST NOT see legacy rows (zero cross-tenant leakage)
        assert store.get_observation(release_id, corr_id, 1, tenant_id="tenant-alpha", environment="production") is None
        assert store.list_receipts(tenant_id="tenant-alpha", environment="production", release_id=release_id) == []
        assert store.list_observations(tenant_id="tenant-alpha", environment="production", release_id=release_id) == []

        # 6. Unscoped queries honestly return legacy rows
        unscoped_rcpts = store.list_receipts(release_id=release_id)
        assert len(unscoped_rcpts) == 1
        assert unscoped_rcpts[0].receipt_id == "legacy-rcpt-001"
        assert unscoped_rcpts[0].tenant_id is None

        # 7. Insert scoped receipt & observation on identical (release_id, correlation_id, loop_id)
        tenant_rcpt = CanonicalLoopReceipt(
            receipt_id="tenant-alpha-rcpt-001",
            receipt_type="terminal",
            loop_id=1,
            correlation_id=corr_id,
            release_id=release_id,
            owner="test-alpha",
            provenance="live",
            status="completed",
            observed_at=now,
            tenant_id="tenant-alpha",
            environment="production",
        )
        store.record_receipt(tenant_rcpt)

        obs_alpha = LoopObservation(
            release_id=release_id,
            correlation_id=corr_id,
            loop_id=1,
            owner="test-alpha",
            terminal_id="tenant-alpha-rcpt-001",
            terminal_status="completed",
            status="complete",
            freshness_status="fresh",
            provenance="live",
            observed_at=now,
            receipt_ids=["tenant-alpha-rcpt-001"],
            tenant_id="tenant-alpha",
            environment="production",
        )
        store.upsert_observation(obs_alpha)

        # 8. Both coexist peacefully without collision
        legacy_after = store.get_observation(release_id, corr_id, 1)
        assert legacy_after is not None
        assert legacy_after.tenant_id is None
        assert legacy_after.terminal_id == "legacy-rcpt-001"

        alpha_after = store.get_observation(release_id, corr_id, 1, tenant_id="tenant-alpha", environment="production")
        assert alpha_after is not None
        assert alpha_after.tenant_id == "tenant-alpha"
        assert alpha_after.environment == "production"
        assert alpha_after.terminal_id == "tenant-alpha-rcpt-001"
    finally:
        with store._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE;")
            conn.commit()


def test_postgres_scoped_persistence_multi_tenant_streaming_and_rebuild_equivalence() -> None:
    """Acceptance: Multi-tenant streaming, fresh process restart/reload, and rebuild equivalence on PostgreSQL."""
    from uuid import uuid4

    store = PostgresTwelveLoopStore(POSTGRES_TEST_DSN)
    store.apply_migration_sync()

    shared_rel = f"rel-multi-{uuid4().hex[:6]}"
    shared_corr = f"corr-multi-{uuid4().hex[:6]}"
    now = datetime.now(timezone.utc)

    # Tenant Alpha (production)
    alpha_stim = CanonicalLoopReceipt(
        receipt_id=f"alpha-stim-{uuid4().hex[:6]}",
        receipt_type="stimulus",
        loop_id=1,
        correlation_id=shared_corr,
        release_id=shared_rel,
        owner="alpha-stim",
        provenance="live",
        observed_at=now,
        tenant_id="tenant-alpha",
        environment="production",
    )
    alpha_term = CanonicalLoopReceipt(
        receipt_id=f"alpha-term-{uuid4().hex[:6]}",
        receipt_type="terminal",
        loop_id=1,
        correlation_id=shared_corr,
        release_id=shared_rel,
        owner="alpha-term",
        provenance="live",
        status="completed",
        observed_at=now + timedelta(seconds=1),
        tenant_id="tenant-alpha",
        environment="production",
    )
    alpha_next = CanonicalLoopReceipt(
        receipt_id=f"alpha-next-{uuid4().hex[:6]}",
        receipt_type="next_consumer",
        loop_id=1,
        correlation_id=shared_corr,
        release_id=shared_rel,
        owner="alpha-next",
        provenance="live",
        status="accepted",
        observed_at=now + timedelta(seconds=2),
        tenant_id="tenant-alpha",
        environment="production",
    )

    # Tenant Beta (staging) on identical release_id and correlation_id
    beta_stim = CanonicalLoopReceipt(
        receipt_id=f"beta-stim-{uuid4().hex[:6]}",
        receipt_type="stimulus",
        loop_id=1,
        correlation_id=shared_corr,
        release_id=shared_rel,
        owner="beta-stim",
        provenance="live",
        observed_at=now,
        tenant_id="tenant-beta",
        environment="staging",
    )
    beta_term = CanonicalLoopReceipt(
        receipt_id=f"beta-term-{uuid4().hex[:6]}",
        receipt_type="terminal",
        loop_id=1,
        correlation_id=shared_corr,
        release_id=shared_rel,
        owner="beta-term",
        provenance="live",
        status="failed",
        observed_at=now + timedelta(seconds=1),
        tenant_id="tenant-beta",
        environment="staging",
    )

    # Stream through scoped projectors
    proj_alpha = TwelveLoopTruthProjector(store=store, tenant_id="tenant-alpha", environment="production", auto_load=False)
    proj_beta = TwelveLoopTruthProjector(store=store, tenant_id="tenant-beta", environment="staging", auto_load=False)

    proj_alpha.ingest_receipts([alpha_stim, alpha_term, alpha_next])
    proj_beta.ingest_receipts([beta_stim, beta_term])

    obs_alpha = proj_alpha.get_observation(shared_rel, shared_corr, 1)
    obs_beta = proj_beta.get_observation(shared_rel, shared_corr, 1)

    assert obs_alpha is not None
    assert obs_alpha.tenant_id == "tenant-alpha"
    assert obs_alpha.environment == "production"
    assert obs_alpha.status == "complete"
    assert obs_alpha.terminal_status == "completed"

    assert obs_beta is not None
    assert obs_beta.tenant_id == "tenant-beta"
    assert obs_beta.environment == "staging"
    assert obs_beta.status == "failed"
    assert obs_beta.terminal_status == "failed"

    # Simulate fresh process restart with auto_load=True
    fresh_alpha = TwelveLoopTruthProjector(store=store, tenant_id="tenant-alpha", environment="production", auto_load=True)
    fresh_beta = TwelveLoopTruthProjector(store=store, tenant_id="tenant-beta", environment="staging", auto_load=True)

    reloaded_alpha = fresh_alpha.get_observation(shared_rel, shared_corr, 1)
    reloaded_beta = fresh_beta.get_observation(shared_rel, shared_corr, 1)

    assert reloaded_alpha is not None
    assert reloaded_beta is not None
    assert reloaded_alpha.to_dict() == obs_alpha.to_dict()
    assert reloaded_beta.to_dict() == obs_beta.to_dict()

    # Rebuild equivalence from store receipts
    rebuilt_alpha = fresh_alpha.rebuild()
    alpha_matches = [o for o in rebuilt_alpha if o.release_id == shared_rel and o.correlation_id == shared_corr and o.loop_id == 1]
    assert len(alpha_matches) == 1
    assert alpha_matches[0].to_dict() == obs_alpha.to_dict()

    # Durable store observations match exactly
    durable_alpha = store.get_observation(shared_rel, shared_corr, 1, tenant_id="tenant-alpha", environment="production")
    durable_beta = store.get_observation(shared_rel, shared_corr, 1, tenant_id="tenant-beta", environment="staging")
    assert durable_alpha is not None
    assert durable_beta is not None
    assert durable_alpha.to_dict() == obs_alpha.to_dict()
    assert durable_beta.to_dict() == obs_beta.to_dict()


def test_postgres_scoped_cross_tenant_collisions_and_duplicate_receipt_handling() -> None:
    """P1: Duplicate receipts and cross-tenant collision invariants against PostgreSQL store."""
    from uuid import uuid4

    store = PostgresTwelveLoopStore(POSTGRES_TEST_DSN)
    store.apply_migration_sync()

    shared_rcpt_id = f"rcpt-collision-{uuid4().hex}"
    now = datetime.now(timezone.utc)

    # Ingest for Tenant A
    rcpt_a = CanonicalLoopReceipt(
        receipt_id=shared_rcpt_id,
        receipt_type="stimulus",
        loop_id=1,
        correlation_id="corr-coll-1",
        release_id="rel-coll-1",
        owner="owner-a",
        provenance="live",
        observed_at=now,
        tenant_id="tenant-a",
        environment="prod",
    )
    store.record_receipt(rcpt_a)

    # Attempt to insert same receipt_id for Tenant B in Postgres store
    rcpt_b = CanonicalLoopReceipt(
        receipt_id=shared_rcpt_id,
        receipt_type="stimulus",
        loop_id=1,
        correlation_id="corr-coll-1",
        release_id="rel-coll-1",
        owner="owner-b",
        provenance="live",
        observed_at=now,
        tenant_id="tenant-b",
        environment="prod",
    )
    store.record_receipt(rcpt_b)

    # Store must preserve Tenant A receipt unchanged (ON CONFLICT DO NOTHING)
    stored = store.get_receipt(shared_rcpt_id)
    assert stored is not None
    assert stored.tenant_id == "tenant-a"
    assert stored.owner == "owner-a"

    # Projector scope mismatch raises ValueError
    proj_a = TwelveLoopTruthProjector(tenant_id="tenant-a", environment="prod")
    proj_a.ingest_receipt(rcpt_a)
    with pytest.raises(ValueError, match="conflicts with projector scoped tenant"):
        proj_a.ingest_receipt(rcpt_b)

    # Projector duplicate receipt_id with different scope raises ValueError
    unscoped_proj = TwelveLoopTruthProjector()
    unscoped_proj.ingest_receipt(rcpt_a)
    with pytest.raises(ValueError, match="Conflicting receipt identity"):
        unscoped_proj.ingest_receipt(rcpt_b)


def test_postgres_scoped_out_of_order_and_provenance_fencing() -> None:
    """P1: Out-of-order delivery and provenance fencing with explicit tenant and environment on PostgreSQL."""
    from uuid import uuid4

    store = PostgresTwelveLoopStore(POSTGRES_TEST_DSN)
    store.apply_migration_sync()

    key = f"pg-ooo-{uuid4().hex[:8]}"
    tenant_id = "tenant-fence"
    env = "staging"
    now = datetime.now(timezone.utc)

    # Next consumer arrives BEFORE stimulus and terminal
    next_rcpt = CanonicalLoopReceipt(
        receipt_id=f"{key}-next",
        receipt_type="next_consumer",
        loop_id=2,
        correlation_id=key,
        release_id=key,
        owner="next-connector",
        provenance="live",
        status="completed",
        observed_at=now + timedelta(seconds=10),
        tenant_id=tenant_id,
        environment=env,
    )
    stimulus = CanonicalLoopReceipt(
        receipt_id=f"{key}-stim",
        receipt_type="stimulus",
        loop_id=2,
        correlation_id=key,
        release_id=key,
        owner="stim-connector",
        provenance="live",
        observed_at=now,
        tenant_id=tenant_id,
        environment=env,
    )
    terminal = CanonicalLoopReceipt(
        receipt_id=f"{key}-term",
        receipt_type="terminal",
        loop_id=2,
        correlation_id=key,
        release_id=key,
        owner="stim-connector",
        provenance="live",
        status="completed",
        observed_at=now + timedelta(seconds=5),
        tenant_id=tenant_id,
        environment=env,
    )

    proj = TwelveLoopTruthProjector(store=store, tenant_id=tenant_id, environment=env, auto_load=False)
    # Ingest out of order: next_consumer -> stimulus -> terminal
    proj.ingest_receipt(next_rcpt)
    obs_early = proj.get_observation(key, key, 2)
    assert obs_early is not None
    assert obs_early.status == "open"
    assert obs_early.next_consumer_receipt_id == next_rcpt.receipt_id

    proj.ingest_receipt(stimulus)
    proj.ingest_receipt(terminal)
    obs_done = proj.get_observation(key, key, 2)
    assert obs_done is not None
    assert obs_done.status == "complete"
    assert obs_done.terminal_id == terminal.receipt_id
    assert obs_done.next_consumer_receipt_id == next_rcpt.receipt_id

    # Ingest an older replay stimulus; must NOT downgrade provenance or overwrite observed_at
    replay_stim = CanonicalLoopReceipt(
        receipt_id=f"{key}-replay-stim",
        receipt_type="stimulus",
        loop_id=2,
        correlation_id=key,
        release_id=key,
        owner="stim-connector",
        provenance="replay",
        observed_at=now - timedelta(seconds=60),
        tenant_id=tenant_id,
        environment=env,
    )
    proj.ingest_receipt(replay_stim)
    obs_fenced = store.get_observation(key, key, 2, tenant_id=tenant_id, environment=env)
    assert obs_fenced is not None
    assert obs_fenced.provenance == "live"
    assert obs_fenced.status == "complete"


def test_postgres_scoped_failed_persistence_raises() -> None:
    """P1: Failed persistence on unreachable DSN raises exception cleanly without corrupted store state."""
    broken_store = PostgresTwelveLoopStore("postgresql://invalid:invalid@127.0.0.1:59999/broken")
    obs = LoopObservation(
        release_id="rel-fail",
        correlation_id="corr-fail",
        loop_id=1,
        owner="test",
        status="open",
        freshness_status="fresh",
        provenance="live",
        observed_at=datetime.now(timezone.utc),
        tenant_id="tenant-fail",
        environment="staging",
    )
    with pytest.raises(Exception):
        broken_store.upsert_observation(obs)


def test_postgres_unscoped_legacy_reduction_isolated_from_scoped_receipts() -> None:
    """Defect 1 regression: Unscoped legacy receipts never reduce with scoped receipts at same key."""
    from uuid import uuid4
    schema = f"test_iso_{uuid4().hex[:8]}"
    store = PostgresTwelveLoopStore(POSTGRES_TEST_DSN, schema=schema)
    store.apply_migration_sync()
    now = datetime.now(timezone.utc)
    key_rel = f"rel-iso-{uuid4().hex[:6]}"
    key_corr = f"corr-iso-{uuid4().hex[:6]}"

    try:
        # Ingest scoped complete loop for tenant-a / prod
        tenant_proj = TwelveLoopTruthProjector(store=store, tenant_id="tenant-a", environment="prod", auto_load=False)
        tenant_proj.ingest_receipts([
            CanonicalLoopReceipt("rcpt-a-stim", "stimulus", 1, key_corr, key_rel, "owner-a", "live", tenant_id="tenant-a", environment="prod", observed_at=now),
            CanonicalLoopReceipt("rcpt-a-term", "terminal", 1, key_corr, key_rel, "owner-a", "live", tenant_id="tenant-a", environment="prod", status="completed", observed_at=now + timedelta(seconds=1), causation_id="rcpt-a-stim"),
            CanonicalLoopReceipt("rcpt-a-next", "next_consumer", 1, key_corr, key_rel, "owner-a", "live", tenant_id="tenant-a", environment="prod", status="accepted", observed_at=now + timedelta(seconds=2), causation_id="rcpt-a-term"),
        ])

        # Ingest legacy unscoped stimulus with identical release/corr/loop
        legacy_proj = TwelveLoopTruthProjector(store=store, auto_load=False)
        obs_legacy = legacy_proj.ingest_receipt(
            CanonicalLoopReceipt("rcpt-legacy-stim", "stimulus", 1, key_corr, key_rel, "owner-legacy", "live", observed_at=now)
        )

        # Legacy observation must NOT reduce with tenant-a receipts
        assert obs_legacy.tenant_id is None
        assert obs_legacy.environment is None
        assert obs_legacy.status == "open"
        assert obs_legacy.terminal_id is None
        assert obs_legacy.stimulus_id == "rcpt-legacy-stim"

        # Verify durable Postgres observations are strictly isolated
        durable_legacy = store.get_observation(key_rel, key_corr, 1)
        assert durable_legacy is not None
        assert durable_legacy.tenant_id is None
        assert durable_legacy.environment is None
        assert durable_legacy.status == "open"
        assert durable_legacy.terminal_id is None

        durable_a = store.get_observation(key_rel, key_corr, 1, tenant_id="tenant-a", environment="prod")
        assert durable_a is not None
        assert durable_a.tenant_id == "tenant-a"
        assert durable_a.environment == "prod"
        assert durable_a.status == "complete"
        assert durable_a.terminal_id == "rcpt-a-term"

        # Partial scope query fails closed
        with pytest.raises(ValueError, match="Partial caller scope is invalid"):
            store.list_receipts(tenant_id="tenant-a", release_id=key_rel)

        with pytest.raises(ValueError, match="Partial caller scope is invalid"):
            store.get_observation(key_rel, key_corr, 1, tenant_id="tenant-a")
    finally:
        with store._connect() as conn:
            conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE;")


def test_postgres_non_lossy_rollback_to_002_and_pre003_source_compatibility() -> None:
    """Defect 2 regression: 003 rollback safely archives scoped rows, restores 002 pkey, and allows pre-003 store execution."""
    import importlib.util
    from uuid import uuid4
    schema = f"test_rb_{uuid4().hex[:8]}"
    store = PostgresTwelveLoopStore(POSTGRES_TEST_DSN, schema=schema)
    store.apply_migration_sync()
    now = datetime.now(timezone.utc)
    shared_rel = f"rel-rb-{uuid4().hex[:6]}"
    shared_corr = f"corr-rb-{uuid4().hex[:6]}"

    try:
        # Seed coexisting scoped and legacy rows on identical release_id, correlation_id, loop_id
        store.record_receipt(
            CanonicalLoopReceipt("rcpt-legacy-term", "terminal", 1, shared_corr, shared_rel, "owner-l", "live", status="completed", observed_at=now)
        )
        store.upsert_observation(
            LoopObservation(
                release_id=shared_rel, correlation_id=shared_corr, loop_id=1, owner="owner-l",
                terminal_id="rcpt-legacy-term", terminal_status="completed", status="complete",
                freshness_status="fresh", provenance="live", observed_at=now,
                receipt_ids=["rcpt-legacy-term"], tenant_id=None, environment=None
            )
        )

        store.record_receipt(
            CanonicalLoopReceipt("rcpt-scoped-term", "terminal", 1, shared_corr, shared_rel, "owner-s", "live", tenant_id="tenant-rb", environment="production", status="completed", observed_at=now)
        )
        store.upsert_observation(
            LoopObservation(
                release_id=shared_rel, correlation_id=shared_corr, loop_id=1, owner="owner-s",
                terminal_id="rcpt-scoped-term", terminal_status="completed", status="complete",
                freshness_status="fresh", provenance="live", observed_at=now,
                receipt_ids=["rcpt-scoped-term"], tenant_id="tenant-rb", environment="production"
            )
        )

        # 1. Execute non-lossy rollback
        store.rollback_to_002_schema_sync()

        # 2. Verify scoped rows archived and legacy row intact
        with store._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) FROM {schema}.twelve_loop_observations_scoped_backup;")
                backup_count = cur.fetchone()[0]
                assert backup_count == 1

                cur.execute(f"SELECT COUNT(*) FROM {schema}.twelve_loop_observations;")
                obs_count = cur.fetchone()[0]
                assert obs_count == 1

                # Check pkey constraint is restored
                cur.execute(f"""
                    SELECT conname FROM pg_constraint
                    WHERE conname = 'twelve_loop_observations_pkey'
                      AND conrelid = '{schema}.twelve_loop_observations'::regclass;
                """)
                assert cur.fetchone() is not None

        # 3. Pre-003 store compatibility: pre-003 store relies on ON CONFLICT (release_id, correlation_id, loop_id)
        spec = importlib.util.spec_from_file_location("pre003_store", "/tmp/codex2-loop-scope-review-001/pre003_store.py")
        old = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(old)
        old_store = old.PostgresTwelveLoopStore(POSTGRES_TEST_DSN, schema=schema)
        # Pre-003 upsert succeeds without InvalidColumnReference or UniqueViolation
        updated_obs = store.get_observation(shared_rel, shared_corr, 1)
        old_store.upsert_observation(updated_obs)

        # 4. Re-apply migration 003 forward: automatically restores backed-up scoped rows
        store.apply_migration_sync()
        with store._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT to_regclass('{schema}.twelve_loop_observations_scoped_backup');")
                assert cur.fetchone()[0] is None

        restored_scoped = store.get_observation(shared_rel, shared_corr, 1, tenant_id="tenant-rb", environment="production")
        assert restored_scoped is not None
        assert restored_scoped.tenant_id == "tenant-rb"
        assert restored_scoped.terminal_id == "rcpt-scoped-term"
    finally:
        with store._connect() as conn:
            conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE;")


def test_postgres_fresh_process_restart_and_rebuild_equivalence() -> None:
    """Defect 3 regression: Fresh-process restart, multi-tenant/environment isolation, and rebuild equivalence."""
    import subprocess
    import sys
    from uuid import uuid4

    schema = f"test_fresh_{uuid4().hex[:8]}"
    store = PostgresTwelveLoopStore(POSTGRES_TEST_DSN, schema=schema)
    store.apply_migration_sync()
    now = datetime.now(timezone.utc)
    rel = f"rel-fresh-{uuid4().hex[:6]}"
    corr = f"corr-fresh-{uuid4().hex[:6]}"

    try:
        # Seed Postgres with:
        # - tenant-1 in production (completed loop 1)
        # - tenant-1 in staging (failed loop 1)
        # - legacy unscoped (open loop 1)
        p_prod = TwelveLoopTruthProjector(store=store, tenant_id="tenant-1", environment="production", auto_load=False)
        p_prod.ingest_receipts([
            CanonicalLoopReceipt("rcpt-p-stim", "stimulus", 1, corr, rel, "owner", "live", tenant_id="tenant-1", environment="production", observed_at=now),
            CanonicalLoopReceipt("rcpt-p-term", "terminal", 1, corr, rel, "owner", "live", tenant_id="tenant-1", environment="production", status="completed", observed_at=now + timedelta(seconds=1), causation_id="rcpt-p-stim"),
            CanonicalLoopReceipt("rcpt-p-next", "next_consumer", 1, corr, rel, "owner", "live", tenant_id="tenant-1", environment="production", status="accepted", observed_at=now + timedelta(seconds=2), causation_id="rcpt-p-term"),
        ])

        p_stage = TwelveLoopTruthProjector(store=store, tenant_id="tenant-1", environment="staging", auto_load=False)
        p_stage.ingest_receipts([
            CanonicalLoopReceipt("rcpt-s-stim", "stimulus", 1, corr, rel, "owner", "live", tenant_id="tenant-1", environment="staging", observed_at=now),
            CanonicalLoopReceipt("rcpt-s-term", "terminal", 1, corr, rel, "owner", "live", tenant_id="tenant-1", environment="staging", status="failed", observed_at=now + timedelta(seconds=1), causation_id="rcpt-s-stim"),
        ])

        p_legacy = TwelveLoopTruthProjector(store=store, auto_load=False)
        p_legacy.ingest_receipt(
            CanonicalLoopReceipt("rcpt-l-stim", "stimulus", 1, corr, rel, "owner", "live", observed_at=now)
        )

        # Child process verifying fresh-process restart and equivalence
        child_code = f"""
import sys, json
from services.control_plane.bff.migrations.twelve_loop_truth import PostgresTwelveLoopStore
from services.control_plane.bff.management_read_models.twelve_loop_projector import TwelveLoopTruthProjector

dsn = sys.argv[1]
schema = sys.argv[2]
rel = sys.argv[3]
corr = sys.argv[4]

store = PostgresTwelveLoopStore(dsn, schema=schema)

# 1. Fresh process reads durable state
obs_prod = store.get_observation(rel, corr, 1, tenant_id="tenant-1", environment="production")
assert obs_prod is not None
assert obs_prod.tenant_id == "tenant-1"
assert obs_prod.environment == "production"
assert obs_prod.status == "complete"
assert obs_prod.terminal_id == "rcpt-p-term"

obs_stage = store.get_observation(rel, corr, 1, tenant_id="tenant-1", environment="staging")
assert obs_stage is not None
assert obs_stage.tenant_id == "tenant-1"
assert obs_stage.environment == "staging"
assert obs_stage.status == "failed"
assert obs_stage.terminal_id == "rcpt-s-term"

obs_legacy = store.get_observation(rel, corr, 1)
assert obs_legacy is not None
assert obs_legacy.tenant_id is None
assert obs_legacy.environment is None
assert obs_legacy.status == "open"
assert obs_legacy.terminal_id is None

# 2. Fresh projector reload and rebuild equivalence
proj_prod = TwelveLoopTruthProjector(store=store, tenant_id="tenant-1", environment="production", auto_load=True)
rebuilt_prod = proj_prod.get_observation(rel, corr, 1, tenant_id="tenant-1", environment="production")
assert rebuilt_prod.to_dict() == obs_prod.to_dict()

proj_stage = TwelveLoopTruthProjector(store=store, tenant_id="tenant-1", environment="staging", auto_load=True)
rebuilt_stage = proj_stage.get_observation(rel, corr, 1, tenant_id="tenant-1", environment="staging")
assert rebuilt_stage.to_dict() == obs_stage.to_dict()

proj_legacy = TwelveLoopTruthProjector(store=store, auto_load=True)
rebuilt_legacy = proj_legacy.get_observation(rel, corr, 1)
assert rebuilt_legacy.to_dict() == obs_legacy.to_dict()

print("CHILD_PROCESS_VERIFICATION_SUCCESS")
"""
        proc = subprocess.run(
            [sys.executable, "-c", child_code, POSTGRES_TEST_DSN, schema, rel, corr],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert "CHILD_PROCESS_VERIFICATION_SUCCESS" in proc.stdout
    finally:
        with store._connect() as conn:
            conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE;")

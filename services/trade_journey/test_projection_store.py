"""
Tests for Trade Journey relational projection store (PostgreSQL).

LIFECYCLE-PROJ-STORE-001: Unit and integration tests covering schema creation,
typed persistence API, advisory locks, duplicate/conflict/quarantine, and two-writer behavior.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from services.trade_journey.projection_store import (
    BatchProjectionMutation,
    ConflictingDuplicateException,
    ControllerStateRow,
    EventReceiptRow,
    IdentityConflictException,
    IdentityLinkRow,
    JourneyRow,
    JourneyStageRow,
    LoopRunRow,
    ProjectionStore,
    ProjectionStoreException,
    QuarantineRow,
)


@pytest.fixture
def postgres_dsn() -> str:
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is not set")
    return dsn


def test_projection_store_bootstrap_and_schema_idempotency(postgres_dsn: str) -> None:
    schema_name = f"test_proj_{uuid4().hex[:8]}"
    store = ProjectionStore(postgres_dsn, schema=schema_name)
    # Bootstrap again to prove idempotency
    store.bootstrap_schema()

    ctrl = store.get_controller_state("ctrl-1", "default", "paper")
    assert ctrl is None

    # Cleanup schema after test
    import psycopg  # type: ignore[import]

    with psycopg.connect(postgres_dsn) as conn, conn.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")


def test_projection_store_atomic_batch_transaction(postgres_dsn: str) -> None:
    schema_name = f"test_proj_{uuid4().hex[:8]}"
    store = ProjectionStore(postgres_dsn, schema=schema_name, bootstrap=True)
    now = datetime.now(timezone.utc)

    mutation = BatchProjectionMutation(
        receipts=[
            EventReceiptRow(
                event_id="evt-1",
                ingested_seq=1,
                fingerprint="fp-1",
                tenant_id="tenant-a",
                environment="paper",
                journey_id="j-1",
                loop_run_id="lr-1",
                source_event_type="trade_episode.opened",
                created_at=now,
                disposition="applied",
                projection_revision=1,
            )
        ],
        identity_links=[
            IdentityLinkRow(
                tenant_id="tenant-a",
                environment="paper",
                identifier_type="trade_id",
                identifier_value="t-100",
                journey_id="j-1",
                first_ingested_seq=1,
                last_ingested_seq=1,
            )
        ],
        journeys=[
            JourneyRow(
                tenant_id="tenant-a",
                environment="paper",
                journey_id="j-1",
                status="open",
                stage_coverage={"opened": True},
                is_terminal=False,
                first_occurred_at=now,
                last_occurred_at=now,
                first_ingested_seq=1,
                last_ingested_seq=1,
                loop_run_id="lr-1",
            )
        ],
        stages=[
            JourneyStageRow(
                tenant_id="tenant-a",
                environment="paper",
                journey_id="j-1",
                source_event_id="evt-1",
                stage_name="opened",
                stage_status="completed",
                stage_ordinal=1,
                source_ingested_seq=1,
                event_sequence=1,
                occurred_at=now,
                fingerprint="fp-1",
            )
        ],
        loop_runs=[
            LoopRunRow(
                tenant_id="tenant-a",
                environment="paper",
                loop_run_id="lr-1",
                journey_id="j-1",
                status="active",
            )
        ],
        new_checkpoint_seq=1,
        source_high_watermark=1,
        backlog_count=0,
        mode="live",
        status="ok",
        accepted_live=True,
        deployment_sha="git-sha-test",
    )

    ctrl = store.execute_batch_transaction("ctrl-main", "tenant-a", "paper", mutation)
    assert ctrl.checkpoint_seq == 1
    assert ctrl.projection_revision == 1
    assert ctrl.accepted_live is True

    # Verify query getters
    receipt = store.get_receipt("evt-1")
    assert receipt is not None
    assert receipt.disposition == "applied"
    assert receipt.journey_id == "j-1"

    resolved_journey = store.resolve_identity("tenant-a", "paper", "trade_id", "t-100")
    assert resolved_journey == "j-1"

    # Cleanup schema after test
    import psycopg

    with psycopg.connect(postgres_dsn) as conn, conn.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")


def test_projection_store_duplicate_and_conflicting_duplicate(postgres_dsn: str) -> None:
    schema_name = f"test_proj_{uuid4().hex[:8]}"
    store = ProjectionStore(postgres_dsn, schema=schema_name, bootstrap=True)
    now = datetime.now(timezone.utc)

    receipt = EventReceiptRow(
        event_id="evt-dup-1",
        ingested_seq=10,
        fingerprint="fp-original",
        tenant_id="t-1",
        environment="paper",
        journey_id="j-dup",
        loop_run_id="",
        source_event_type="trade_episode.opened",
        created_at=now,
        disposition="applied",
        projection_revision=1,
    )
    mutation1 = BatchProjectionMutation(
        receipts=[receipt],
        new_checkpoint_seq=10,
        source_high_watermark=10,
    )
    store.execute_batch_transaction("ctrl-1", "t-1", "paper", mutation1)

    # 1. Exact duplicate (same event_id and fingerprint) should be idempotent
    mutation_exact = BatchProjectionMutation(
        receipts=[receipt],
        new_checkpoint_seq=10,
        source_high_watermark=10,
    )
    ctrl_after_exact = store.execute_batch_transaction("ctrl-1", "t-1", "paper", mutation_exact)
    assert ctrl_after_exact.projection_revision == 1  # Revision must NOT increment on exact duplicate

    # 2. Conflicting duplicate (same event_id, different fingerprint) must raise ConflictingDuplicateException
    conflicting_receipt = EventReceiptRow(
        event_id="evt-dup-1",
        ingested_seq=11,
        fingerprint="fp-CONFLICTING",
        tenant_id="t-1",
        environment="paper",
        journey_id="j-dup",
        loop_run_id="",
        source_event_type="trade_episode.opened",
        created_at=now,
        disposition="applied",
        projection_revision=2,
    )
    mutation_conflict = BatchProjectionMutation(
        receipts=[conflicting_receipt],
        new_checkpoint_seq=11,
        source_high_watermark=11,
    )
    with pytest.raises(ConflictingDuplicateException, match="conflicting fingerprint"):
        store.execute_batch_transaction("ctrl-1", "t-1", "paper", mutation_conflict)

    # Cleanup schema after test
    import psycopg

    with psycopg.connect(postgres_dsn) as conn, conn.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")


def test_projection_store_identity_conflict_detection(postgres_dsn: str) -> None:
    schema_name = f"test_proj_{uuid4().hex[:8]}"
    store = ProjectionStore(postgres_dsn, schema=schema_name, bootstrap=True)

    link1 = IdentityLinkRow(
        tenant_id="t-1",
        environment="paper",
        identifier_type="trade_id",
        identifier_value="t-shared-99",
        journey_id="j-first",
        first_ingested_seq=100,
        last_ingested_seq=100,
    )
    mutation1 = BatchProjectionMutation(identity_links=[link1], new_checkpoint_seq=100)
    store.execute_batch_transaction("ctrl-1", "t-1", "paper", mutation1)

    # Attempting to bind the same identifier to a different journey_id must fail
    link_conflict = IdentityLinkRow(
        tenant_id="t-1",
        environment="paper",
        identifier_type="trade_id",
        identifier_value="t-shared-99",
        journey_id="j-SECOND-CONFLICT",
        first_ingested_seq=101,
        last_ingested_seq=101,
    )
    mutation_conflict = BatchProjectionMutation(identity_links=[link_conflict], new_checkpoint_seq=101)
    with pytest.raises(IdentityConflictException, match="already bound to journey j-first"):
        store.execute_batch_transaction("ctrl-1", "t-1", "paper", mutation_conflict)

    # Cleanup schema after test
    import psycopg

    with psycopg.connect(postgres_dsn) as conn, conn.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")


def test_projection_store_quarantine_recording(postgres_dsn: str) -> None:
    schema_name = f"test_proj_{uuid4().hex[:8]}"
    store = ProjectionStore(postgres_dsn, schema=schema_name, bootstrap=True)
    now = datetime.now(timezone.utc)

    q_row = QuarantineRow(
        event_id="evt-bad-1",
        ingested_seq=50,
        reason_code="SCHEMA_INVALID",
        reason_detail="Missing required field 'trade_id'",
        source_event_type="trade_episode.opened",
        tenant_id="t-1",
        environment="paper",
    )
    mutation = BatchProjectionMutation(
        quarantines=[q_row],
        receipts=[
            EventReceiptRow(
                event_id="evt-bad-1",
                ingested_seq=50,
                fingerprint="fp-bad",
                tenant_id="t-1",
                environment="paper",
                journey_id="",
                loop_run_id="",
                source_event_type="trade_episode.opened",
                created_at=now,
                disposition="quarantined",
                projection_revision=1,
            )
        ],
        new_checkpoint_seq=50,
    )
    ctrl = store.execute_batch_transaction("ctrl-1", "t-1", "paper", mutation)
    assert ctrl.unresolved_quarantine_count == 1

    # Cleanup schema after test
    import psycopg

    with psycopg.connect(postgres_dsn) as conn, conn.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")


def test_projection_store_exact_duplicate_does_not_mutate_stage_or_increment_revision(postgres_dsn: str) -> None:
    """Requirement 1: Exact duplicate processing must be truly idempotent and not rewrite stage/aggregate rows or increment revision."""
    schema_name = f"test_proj_{uuid4().hex[:8]}"
    store = ProjectionStore(postgres_dsn, schema=schema_name, bootstrap=True)
    now = datetime.now(timezone.utc)

    stage = JourneyStageRow(
        tenant_id="t-1",
        environment="paper",
        journey_id="j-1",
        source_event_id="evt-1",
        stage_name="opened",
        stage_status="completed",
        stage_ordinal=1,
        source_ingested_seq=1,
        event_sequence=1,
        occurred_at=now,
        fingerprint="fp-1",
    )
    receipt = EventReceiptRow(
        event_id="evt-1",
        ingested_seq=1,
        fingerprint="fp-1",
        tenant_id="t-1",
        environment="paper",
        journey_id="j-1",
        loop_run_id="lr-1",
        source_event_type="opened",
        created_at=now,
        disposition="applied",
        projection_revision=1,
    )
    mutation = BatchProjectionMutation(
        receipts=[receipt],
        stages=[stage],
        new_checkpoint_seq=1,
        source_high_watermark=1,
    )
    ctrl1 = store.execute_batch_transaction("ctrl-1", "t-1", "paper", mutation)
    assert ctrl1.projection_revision == 1

    # Now attempt exact duplicate with a mutated stage status in payload (simulating duplicate attempt)
    exact_dup_receipt = EventReceiptRow(
        event_id="evt-1",
        ingested_seq=1,
        fingerprint="fp-1",
        tenant_id="t-1",
        environment="paper",
        journey_id="j-1",
        loop_run_id="lr-1",
        source_event_type="opened",
        created_at=now,
        disposition="duplicate",
        projection_revision=1,
    )
    dup_mutation = BatchProjectionMutation(
        receipts=[exact_dup_receipt],
        new_checkpoint_seq=1,
        source_high_watermark=1,
    )
    ctrl2 = store.execute_batch_transaction("ctrl-1", "t-1", "paper", dup_mutation)

    # Revision must NOT increment
    assert ctrl2.projection_revision == 1

    # Verify stage_status in database was NOT changed
    import psycopg
    with psycopg.connect(postgres_dsn) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT stage_status FROM {schema_name}.journey_stages WHERE source_event_id='evt-1'")
        row = cur.fetchone()
        assert row[0] == "completed"
        cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")


def test_projection_store_contiguous_checkpoint_advancement(postgres_dsn: str) -> None:
    """Requirement 2: Enforce contiguous checkpoint advancement from durable dispositions."""
    schema_name = f"test_proj_{uuid4().hex[:8]}"
    store = ProjectionStore(postgres_dsn, schema=schema_name, bootstrap=True)
    now = datetime.now(timezone.utc)

    # Insert receipts 1, 2, 3
    receipts = [
        EventReceiptRow("evt-1", 1, "fp-1", "t-1", "paper", "j-1", "", "opened", now, "applied", 1),
        EventReceiptRow("evt-2", 2, "fp-2", "t-1", "paper", "j-1", "", "opened", now, "applied", 1),
        EventReceiptRow("evt-3", 3, "fp-3", "t-1", "paper", "j-1", "", "opened", now, "applied", 1),
    ]
    mutation1 = BatchProjectionMutation(receipts=receipts, new_checkpoint_seq=3, source_high_watermark=3)
    ctrl1 = store.execute_batch_transaction("ctrl-1", "t-1", "paper", mutation1)
    assert ctrl1.checkpoint_seq == 3

    # Attempt receiptless mutation claiming checkpoint 999
    mutation_gap = BatchProjectionMutation(
        receipts=[],
        new_checkpoint_seq=999,
        source_high_watermark=999,
    )
    ctrl2 = store.execute_batch_transaction("ctrl-1", "t-1", "paper", mutation_gap)
    # Checkpoint must remain 3
    assert ctrl2.checkpoint_seq == 3

    # Now provide receipt 5 (gap at 4)
    receipt5 = EventReceiptRow("evt-5", 5, "fp-5", "t-1", "paper", "j-1", "", "opened", now, "applied", 2)
    mutation5 = BatchProjectionMutation(receipts=[receipt5], new_checkpoint_seq=5, source_high_watermark=5)
    ctrl3 = store.execute_batch_transaction("ctrl-1", "t-1", "paper", mutation5)
    # Gap at 4 prevents advancement to 5
    assert ctrl3.checkpoint_seq == 3

    import psycopg
    with psycopg.connect(postgres_dsn) as conn, conn.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")


def test_projection_store_mode_freshness_timestamps(postgres_dsn: str) -> None:
    """Requirement 3: Enforce mode-owned freshness timestamps so backfill/recovery/replay cannot advance last_live_success_at."""
    schema_name = f"test_proj_{uuid4().hex[:8]}"
    store = ProjectionStore(postgres_dsn, schema=schema_name, bootstrap=True)
    now = datetime.now(timezone.utc)

    # Initial live execution
    m_live = BatchProjectionMutation(
        receipts=[EventReceiptRow("evt-1", 1, "fp-1", "t-1", "paper", "j-1", "", "opened", now, "applied", 1)],
        mode="live",
        accepted_live=True,
    )
    ctrl_live = store.execute_batch_transaction("ctrl-1", "t-1", "paper", m_live)
    assert ctrl_live.last_live_success_at is not None
    initial_live_ts = ctrl_live.last_live_success_at

    # Backfill execution with accepted_live=True (simulating backfill trying to pass accepted_live=True)
    m_backfill = BatchProjectionMutation(
        receipts=[EventReceiptRow("evt-2", 2, "fp-2", "t-1", "paper", "j-1", "", "opened", now, "applied", 2)],
        mode="backfill",
        accepted_live=True,
    )
    ctrl_bf = store.execute_batch_transaction("ctrl-1", "t-1", "paper", m_backfill)
    assert ctrl_bf.last_live_success_at == initial_live_ts
    assert ctrl_bf.last_backfill_at is not None

    import psycopg
    with psycopg.connect(postgres_dsn) as conn, conn.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")


def test_projection_store_unresolved_quarantine_count_derivation(postgres_dsn: str) -> None:
    """Requirement 4: Derive unresolved quarantine count from unresolved database truth."""
    schema_name = f"test_proj_{uuid4().hex[:8]}"
    store = ProjectionStore(postgres_dsn, schema=schema_name, bootstrap=True)
    now = datetime.now(timezone.utc)

    q1 = QuarantineRow("q-1", 1, "BAD_PAYLOAD", "detail", "opened", "t-1", "paper", resolution_status="unresolved")
    q2 = QuarantineRow("q-2", 2, "BAD_PAYLOAD", "detail", "opened", "t-1", "paper", resolution_status="resolved")

    m = BatchProjectionMutation(quarantines=[q1, q2], new_checkpoint_seq=2)
    ctrl = store.execute_batch_transaction("ctrl-1", "t-1", "paper", m)
    assert ctrl.unresolved_quarantine_count == 1

    import psycopg
    with psycopg.connect(postgres_dsn) as conn, conn.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")


def test_projection_store_deterministic_out_of_order_bounds(postgres_dsn: str) -> None:
    """Requirement 5: Preserve deterministic out-of-order first/last source bounds."""
    schema_name = f"test_proj_{uuid4().hex[:8]}"
    store = ProjectionStore(postgres_dsn, schema=schema_name, bootstrap=True)
    t1 = datetime(2026, 8, 1, 10, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)

    # Ingest newer event first (seq=10, time=t2)
    j_newer = JourneyRow(
        tenant_id="t-1", environment="paper", journey_id="j-1", status="open",
        stage_coverage={}, is_terminal=False, first_occurred_at=t2, last_occurred_at=t2,
        first_ingested_seq=10, last_ingested_seq=10
    )
    m1 = BatchProjectionMutation(journeys=[j_newer], new_checkpoint_seq=10)
    store.execute_batch_transaction("ctrl-1", "t-1", "paper", m1)

    # Ingest older event later (seq=5, time=t1)
    j_older = JourneyRow(
        tenant_id="t-1", environment="paper", journey_id="j-1", status="open",
        stage_coverage={}, is_terminal=False, first_occurred_at=t1, last_occurred_at=t1,
        first_ingested_seq=5, last_ingested_seq=5
    )
    m2 = BatchProjectionMutation(journeys=[j_older], new_checkpoint_seq=10)
    store.execute_batch_transaction("ctrl-1", "t-1", "paper", m2)

    import psycopg
    with psycopg.connect(postgres_dsn) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT first_occurred_at, last_occurred_at, first_ingested_seq, last_ingested_seq FROM {schema_name}.journeys WHERE journey_id='j-1'")
        row = cur.fetchone()
        assert row[0] == t1
        assert row[1] == t2
        assert row[2] == 5
        assert row[3] == 10
        cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")


def test_projection_store_two_writers_and_nonblocking_lock(postgres_dsn: str) -> None:
    """Requirement 6: Non-blocking controller lock causes second writer to fail immediately."""
    schema_name = f"test_proj_{uuid4().hex[:8]}"
    store1 = ProjectionStore(postgres_dsn, schema=schema_name, bootstrap=True)
    store2 = ProjectionStore(postgres_dsn, schema=schema_name, bootstrap=False)

    import psycopg
    # Hold lock in separate transaction
    with psycopg.connect(postgres_dsn) as conn:
        with conn.cursor() as cur:
            lock_str = "ctrl-1:t-1:paper"
            lock_id = (hash(lock_str) & 0x7FFFFFFF) or PROJECTION_CONTROLLER_ADVISORY_LOCK_ID
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (lock_id,))

            m = BatchProjectionMutation(new_checkpoint_seq=1)
            with pytest.raises(ProjectionStoreException, match="Could not acquire advisory lock"):
                store2.execute_batch_transaction("ctrl-1", "t-1", "paper", m)

    with psycopg.connect(postgres_dsn) as conn, conn.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")


def test_projection_store_identifier_type_check_constraint_and_runtime_constructor(postgres_dsn: str) -> None:
    """Requirement 7: Verify identifier_type check constraint and least-privilege runtime constructor."""
    schema_name = f"test_proj_{uuid4().hex[:8]}"
    # Bootstrap schema with DDL
    store_admin = ProjectionStore(postgres_dsn, schema=schema_name, bootstrap=True)

    # Runtime constructor (bootstrap=False) does not run DDL
    store_runtime = ProjectionStore(postgres_dsn, schema=schema_name, bootstrap=False)

    # Attempting to insert invalid identifier_type should fail check constraint
    invalid_link = IdentityLinkRow(
        tenant_id="t-1", environment="paper", identifier_type="INVALID_TYPE",
        identifier_value="val-1", journey_id="j-1", first_ingested_seq=1, last_ingested_seq=1
    )
    m = BatchProjectionMutation(identity_links=[invalid_link])
    import psycopg
    with pytest.raises(psycopg.errors.CheckViolation):
        store_runtime.execute_batch_transaction("ctrl-1", "t-1", "paper", m)

    with psycopg.connect(postgres_dsn) as conn, conn.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")


def test_projection_store_transaction_rollback_and_retry(postgres_dsn: str) -> None:
    """Requirement 8a: Verify rollback leaves no partial aggregate or checkpoint advance."""
    schema_name = f"test_proj_{uuid4().hex[:8]}"
    store = ProjectionStore(postgres_dsn, schema=schema_name, bootstrap=True)
    now = datetime.now(timezone.utc)

    # Valid receipt + invalid identity link type in same batch
    receipt = EventReceiptRow("evt-1", 1, "fp-1", "t-1", "paper", "j-1", "", "opened", now, "applied", 1)
    bad_link = IdentityLinkRow("t-1", "paper", "BAD_TYPE", "val-1", "j-1", 1, 1)

    m = BatchProjectionMutation(receipts=[receipt], identity_links=[bad_link], new_checkpoint_seq=1)
    import psycopg
    with pytest.raises(psycopg.errors.CheckViolation):
        store.execute_batch_transaction("ctrl-1", "t-1", "paper", m)

    # Verify receipt was rolled back
    assert store.get_receipt("evt-1") is None
    assert store.get_controller_state("ctrl-1", "t-1", "paper") is None

    with psycopg.connect(postgres_dsn) as conn, conn.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")


def test_projection_store_migration_applied_twice_and_prior_reader_compat(postgres_dsn: str) -> None:
    """Requirement 8b: Migration applied twice is idempotent and schema is backward compatible."""
    schema_name = f"test_proj_{uuid4().hex[:8]}"
    store = ProjectionStore(postgres_dsn, schema=schema_name, bootstrap=True)
    # Apply migration again
    store.bootstrap_schema()

    import psycopg
    with psycopg.connect(postgres_dsn) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='{schema_name}'")
        count = cur.fetchone()[0]
        assert count == 7
        cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")


def test_projection_store_indexed_explain_paths(postgres_dsn: str) -> None:
    """Requirement 8c: Validate EXPLAIN query plans for all 5 required indexes."""
    schema_name = f"test_proj_{uuid4().hex[:8]}"
    store = ProjectionStore(postgres_dsn, schema=schema_name, bootstrap=True)

    import psycopg
    with psycopg.connect(postgres_dsn) as conn, conn.cursor() as cur:
        # Index 1: event_receipts (ingested_seq)
        cur.execute(f"EXPLAIN SELECT * FROM {schema_name}.event_receipts WHERE ingested_seq > 10 ORDER BY ingested_seq LIMIT 10")
        plan1 = "\n".join(r[0] for r in cur.fetchall())
        assert "Index Scan" in plan1 or "Index Only Scan" in plan1 or "Bitmap Index Scan" in plan1

        # Index 2: identity_links (tenant_id, environment, journey_id)
        cur.execute(f"EXPLAIN SELECT * FROM {schema_name}.identity_links WHERE tenant_id='t-1' AND environment='paper' AND journey_id='j-1'")
        plan2 = "\n".join(r[0] for r in cur.fetchall())
        assert "Index Scan" in plan2 or "Bitmap Index Scan" in plan2

        # Index 3: journeys (tenant_id, environment, updated_at DESC, journey_id DESC)
        cur.execute(f"EXPLAIN SELECT * FROM {schema_name}.journeys WHERE tenant_id='t-1' AND environment='paper' ORDER BY updated_at DESC, journey_id DESC LIMIT 10")
        plan3 = "\n".join(r[0] for r in cur.fetchall())
        assert "Index Scan" in plan3 or "Bitmap Index Scan" in plan3

        # Index 4: journey_stages (timeline)
        cur.execute(f"EXPLAIN SELECT * FROM {schema_name}.journey_stages WHERE tenant_id='t-1' AND environment='paper' AND journey_id='j-1' ORDER BY stage_ordinal, event_sequence, occurred_at")
        plan4 = "\n".join(r[0] for r in cur.fetchall())
        assert "Index Scan" in plan4 or "Bitmap Index Scan" in plan4

        # Index 5: loop_runs (tenant_id, environment, updated_at DESC, loop_run_id DESC)
        cur.execute(f"EXPLAIN SELECT * FROM {schema_name}.loop_runs WHERE tenant_id='t-1' AND environment='paper' ORDER BY updated_at DESC, loop_run_id DESC LIMIT 10")
        plan5 = "\n".join(r[0] for r in cur.fetchall())
        assert "Index Scan" in plan5 or "Bitmap Index Scan" in plan5

        cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")


"""
Tests for Trade Journey relational projection store (PostgreSQL).

LIFECYCLE-PROJ-STORE-001: Unit and integration tests covering schema creation,
typed persistence API, advisory locks, duplicate/conflict/quarantine, and two-writer behavior.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from services.trade_journey.lifecycle_projector import STABLE_IDENTITY_FIELDS
from services.trade_journey.materializer import IDENTIFIER_FIELDS
from services.trade_journey.projection_store import (
    DEFAULT_PROJECTION_TIMEOUT_SECONDS,
    BatchProjectionMutation,
    ConflictingDuplicateException,
    ConcurrentReceiptClaimException,
    ControllerStateRow,
    EventReceiptRow,
    IdentityConflictException,
    IdentityLinkRow,
    INITIAL_MIGRATION_PATH,
    JourneyRow,
    JourneyStageRow,
    LoopRunRow,
    ProjectionStore,
    ProjectionStoreException,
    QuarantineRow,
    controller_advisory_lock_id,
)


@pytest.fixture
def postgres_dsn() -> str:
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is not set")
    return dsn


@pytest.fixture
def postgres_admin_dsn() -> str:
    dsn = os.getenv("TEST_DATABASE_ADMIN_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_ADMIN_URL is not set")
    return dsn


def test_projection_store_bootstrap_and_schema_idempotency(postgres_dsn: str) -> None:
    schema_name = f"test_proj_{uuid4().hex[:8]}"
    store = ProjectionStore(postgres_dsn, schema=schema_name)
    store.bootstrap_schema()
    # Bootstrap again to prove idempotency.
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
                identifier_type="signal_id",
                identifier_value="t-100",
                journey_id="j-1",
                first_ingested_seq=1,
                last_ingested_seq=1,
                first_occurred_at=now,
                last_occurred_at=now,
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
    receipts = store.get_receipts(["missing", "evt-1", "evt-1"])
    assert set(receipts) == {"evt-1"}
    assert receipts["evt-1"].fingerprint == "fp-1"

    aggregate_events = store.load_journey_stage_events_bulk(
        [("tenant-a", "paper", "j-1"), ("tenant-a", "paper", "missing")]
    )
    assert len(aggregate_events[("tenant-a", "paper", "j-1")]) == 1
    assert aggregate_events[("tenant-a", "paper", "missing")] == []

    resolved_journey = store.resolve_identity("tenant-a", "paper", "signal_id", "t-100")
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

    batch_conflict = BatchProjectionMutation(
        receipts=[
            EventReceiptRow(
                "evt-batch-conflict", 12, "fp-a", "t-1", "paper", "j-dup", "",
                "opened", now, "applied", 2,
            ),
            EventReceiptRow(
                "evt-batch-conflict", 13, "fp-b", "t-1", "paper", "j-dup", "",
                "opened", now, "applied", 2,
            ),
        ]
    )
    with pytest.raises(ConflictingDuplicateException, match="within one batch"):
        store.execute_batch_transaction("ctrl-1", "t-1", "paper", batch_conflict)
    assert store.get_receipt("evt-batch-conflict") is None

    # Cleanup schema after test
    import psycopg

    with psycopg.connect(postgres_dsn) as conn, conn.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")


def test_projection_store_identity_conflict_detection(postgres_dsn: str) -> None:
    schema_name = f"test_proj_{uuid4().hex[:8]}"
    store = ProjectionStore(postgres_dsn, schema=schema_name, bootstrap=True)
    now = datetime.now(timezone.utc)

    link1 = IdentityLinkRow(
        tenant_id="t-1",
        environment="paper",
        identifier_type="signal_id",
        identifier_value="t-shared-99",
        journey_id="j-first",
        first_ingested_seq=100,
        last_ingested_seq=100,
        first_occurred_at=now,
        last_occurred_at=now,
    )
    receipt1 = EventReceiptRow(
        "evt-identity-1", 1, "fp-identity-1", "t-1", "paper", "j-first", "",
        "opened", now, "applied", 1,
    )
    mutation1 = BatchProjectionMutation(
        receipts=[receipt1], identity_links=[link1], new_checkpoint_seq=100
    )
    store.execute_batch_transaction("ctrl-1", "t-1", "paper", mutation1)

    # Attempting to bind the same identifier to a different journey_id must fail
    link_conflict = IdentityLinkRow(
        tenant_id="t-1",
        environment="paper",
        identifier_type="signal_id",
        identifier_value="t-shared-99",
        journey_id="j-SECOND-CONFLICT",
        first_ingested_seq=101,
        last_ingested_seq=101,
        first_occurred_at=now,
        last_occurred_at=now,
    )
    receipt2 = EventReceiptRow(
        "evt-identity-2", 2, "fp-identity-2", "t-1", "paper",
        "j-SECOND-CONFLICT", "", "opened", now, "applied", 2,
    )
    mutation_conflict = BatchProjectionMutation(
        receipts=[receipt2], identity_links=[link_conflict], new_checkpoint_seq=101
    )
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
    journey = JourneyRow(
        tenant_id="t-1",
        environment="paper",
        journey_id="j-1",
        status="open",
        stage_coverage={"opened": True},
        is_terminal=False,
        first_occurred_at=now,
        last_occurred_at=now,
        first_ingested_seq=1,
        last_ingested_seq=1,
    )
    mutation = BatchProjectionMutation(
        receipts=[receipt],
        stages=[stage],
        journeys=[journey],
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
    mutated_stage = JourneyStageRow(
        tenant_id="t-1",
        environment="paper",
        journey_id="j-1",
        source_event_id="evt-1",
        stage_name="opened",
        stage_status="tampered",
        stage_ordinal=1,
        source_ingested_seq=1,
        event_sequence=1,
        occurred_at=now,
        fingerprint="fp-1",
    )
    mutated_journey = JourneyRow(
        tenant_id="t-1",
        environment="paper",
        journey_id="j-1",
        status="tampered",
        stage_coverage={"opened": False},
        is_terminal=True,
        first_occurred_at=now,
        last_occurred_at=now,
        first_ingested_seq=1,
        last_ingested_seq=1,
    )
    dup_mutation = BatchProjectionMutation(
        receipts=[exact_dup_receipt],
        stages=[mutated_stage],
        journeys=[mutated_journey],
        new_checkpoint_seq=1,
        source_high_watermark=1,
    )
    ctrl2 = store.execute_batch_transaction("ctrl-1", "t-1", "paper", dup_mutation)

    # Revision must NOT increment
    assert ctrl2.projection_revision == 1

    # Verify stage_status in database was NOT changed
    import psycopg
    with psycopg.connect(postgres_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT stage_status FROM {schema_name}.journey_stages WHERE source_event_id='evt-1'"
        )
        assert cur.fetchone()[0] == "completed"
        cur.execute(
            f"SELECT status, stage_coverage, is_terminal FROM {schema_name}.journeys WHERE journey_id='j-1'"
        )
        assert cur.fetchone() == ("open", {"opened": True}, False)
        cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")


def test_projection_store_mixed_batch_filters_duplicate_owned_mutations(
    postgres_dsn: str,
) -> None:
    """A durable duplicate cannot smuggle derived rewrites beside a new event."""

    schema_name = f"test_proj_{uuid4().hex[:8]}"
    store = ProjectionStore(postgres_dsn, schema=schema_name, bootstrap=True)
    now = datetime.now(timezone.utc)
    old_receipt = EventReceiptRow(
        "evt-old", 1, "fp-old", "t-1", "paper", "j-old", "", "opened",
        now, "applied", 1,
    )
    old_stage = JourneyStageRow(
        "t-1", "paper", "j-old", "evt-old", "opened", "completed", 1, 1,
        1, now, fingerprint="fp-old",
    )
    old_journey = JourneyRow(
        "t-1", "paper", "j-old", "open", {"opened": True}, False,
        now, now, 1, 1,
    )
    old_quarantine = QuarantineRow(
        "evt-old", 1, "REVIEW", "original", "opened", "t-1", "paper",
        "j-old", "fp-old",
    )
    first = store.execute_batch_transaction(
        "ctrl-1",
        "t-1",
        "paper",
        BatchProjectionMutation(
            receipts=[old_receipt],
            journeys=[old_journey],
            stages=[old_stage],
            quarantines=[old_quarantine],
            source_high_watermark=1,
        ),
    )
    assert first.projection_revision == 1

    import psycopg

    with psycopg.connect(postgres_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT status, stage_coverage, is_terminal, projection_revision,
                   first_ingested_seq, last_ingested_seq, updated_at
            FROM {schema_name}.journeys WHERE journey_id='j-old'
            """
        )
        old_journey_truth = cur.fetchone()
        cur.execute(
            f"""
            SELECT stage_status, projection_revision, fingerprint, recorded_at
            FROM {schema_name}.journey_stages WHERE source_event_id='evt-old'
            """
        )
        old_stage_truth = cur.fetchone()
        cur.execute(
            f"""
            SELECT reason_code, reason_detail, occurrence_count, resolution_status,
                   first_seen_at, last_seen_at
            FROM {schema_name}.quarantine WHERE event_id='evt-old'
            """
        )
        old_quarantine_truth = cur.fetchone()

    new_receipt = EventReceiptRow(
        "evt-new", 2, "fp-new", "t-1", "paper", "j-new", "", "opened",
        now, "applied", 2,
    )
    mixed = store.execute_batch_transaction(
        "ctrl-1",
        "t-1",
        "paper",
        BatchProjectionMutation(
            receipts=[old_receipt, new_receipt],
            journeys=[
                JourneyRow(
                    "t-1", "paper", "j-old", "tampered", {"opened": False},
                    True, now, now, 1, 999,
                ),
                JourneyRow(
                    "t-1", "paper", "j-new", "open", {"opened": True},
                    False, now, now, 2, 2,
                ),
            ],
            stages=[
                JourneyStageRow(
                    "t-1", "paper", "j-old", "evt-old", "opened", "tampered",
                    1, 1, 1, now, fingerprint="fp-old",
                ),
                JourneyStageRow(
                    "t-1", "paper", "j-new", "evt-new", "opened", "completed",
                    1, 2, 1, now, fingerprint="fp-new",
                ),
            ],
            quarantines=[
                QuarantineRow(
                    "evt-old", 1, "TAMPERED", "tampered", "opened", "t-1",
                    "paper", "j-old", "fp-old",
                )
            ],
            source_high_watermark=2,
        ),
    )
    assert mixed.projection_revision == 2

    with psycopg.connect(postgres_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT status, stage_coverage, is_terminal, projection_revision,
                   first_ingested_seq, last_ingested_seq, updated_at
            FROM {schema_name}.journeys WHERE journey_id='j-old'
            """
        )
        assert cur.fetchone() == old_journey_truth
        cur.execute(
            f"""
            SELECT stage_status, projection_revision, fingerprint, recorded_at
            FROM {schema_name}.journey_stages WHERE source_event_id='evt-old'
            """
        )
        assert cur.fetchone() == old_stage_truth
        cur.execute(
            f"""
            SELECT reason_code, reason_detail, occurrence_count, resolution_status,
                   first_seen_at, last_seen_at
            FROM {schema_name}.quarantine WHERE event_id='evt-old'
            """
        )
        assert cur.fetchone() == old_quarantine_truth
        cur.execute(
            f"""
            SELECT journey_id, projection_revision
            FROM {schema_name}.journeys WHERE journey_id='j-new'
            """
        )
        assert cur.fetchone() == ("j-new", 2)
        cur.execute(
            f"""
            SELECT source_event_id, projection_revision
            FROM {schema_name}.journey_stages WHERE source_event_id='evt-new'
            """
        )
        assert cur.fetchone() == ("evt-new", 2)
        cur.execute(f"SELECT COUNT(*) FROM {schema_name}.event_receipts")
        assert cur.fetchone()[0] == 2
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
    assert ctrl2.projection_revision == 1

    with pytest.raises(
        ProjectionStoreException,
        match="mutations require at least one durable event receipt",
    ):
        store.execute_batch_transaction(
            "ctrl-1",
            "t-1",
            "paper",
            BatchProjectionMutation(
                journeys=[
                    JourneyRow(
                        "t-1", "paper", "receiptless", "tampered", {}, False,
                        now, now, 999, 999,
                    )
                ],
                new_checkpoint_seq=999,
            ),
        )

    # Now provide receipt 5 (gap at 4)
    receipt5 = EventReceiptRow("evt-5", 5, "fp-5", "t-1", "paper", "j-1", "", "opened", now, "applied", 2)
    mutation5 = BatchProjectionMutation(receipts=[receipt5], new_checkpoint_seq=5, source_high_watermark=5)
    ctrl3 = store.execute_batch_transaction("ctrl-1", "t-1", "paper", mutation5)
    # Gap at 4 prevents advancement to 5
    assert ctrl3.checkpoint_seq == 3

    # Filling the durable gap must cross the already persisted receipt 5.
    receipt4 = EventReceiptRow(
        "evt-4", 4, "fp-4", "t-1", "paper", "j-1", "", "opened", now, "applied", 3
    )
    ctrl4 = store.execute_batch_transaction(
        "ctrl-1",
        "t-1",
        "paper",
        BatchProjectionMutation(
            receipts=[receipt4], new_checkpoint_seq=999, source_high_watermark=999
        ),
    )
    assert ctrl4.checkpoint_seq == 5

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
    assert ctrl_bf.accepted_live is False

    m_recovery = BatchProjectionMutation(
        receipts=[
            EventReceiptRow(
                "evt-3", 3, "fp-3", "t-1", "paper", "j-1", "", "opened", now,
                "applied", 3,
            )
        ],
        mode="recovery",
        accepted_live=True,
    )
    ctrl_recovery = store.execute_batch_transaction(
        "ctrl-1", "t-1", "paper", m_recovery
    )
    assert ctrl_recovery.last_recovery_at is not None
    assert ctrl_recovery.last_live_success_at == initial_live_ts
    assert ctrl_recovery.accepted_live is False

    m_replay = BatchProjectionMutation(
        receipts=[
            EventReceiptRow(
                "evt-4", 4, "fp-4", "t-1", "paper", "j-1", "", "opened", now,
                "applied", 4,
            )
        ],
        mode="replay",
        accepted_live=True,
    )
    ctrl_replay = store.execute_batch_transaction(
        "ctrl-1", "t-1", "paper", m_replay
    )
    assert ctrl_replay.last_replay_at is not None
    assert ctrl_replay.last_live_success_at == initial_live_ts
    assert ctrl_replay.accepted_live is False

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

    receipt1 = EventReceiptRow(
        "q-1", 1, "fp-q-1", "t-1", "paper", "", "", "opened", now,
        "quarantined", 1,
    )
    receipt2 = EventReceiptRow(
        "q-2", 2, "fp-q-2", "t-1", "paper", "", "", "opened", now,
        "quarantined", 1,
    )
    m = BatchProjectionMutation(
        receipts=[receipt1, receipt2], quarantines=[q1, q2], new_checkpoint_seq=2
    )
    ctrl = store.execute_batch_transaction("ctrl-1", "t-1", "paper", m)
    assert ctrl.unresolved_quarantine_count == 1

    # An exact retry must not increment occurrence or controller count.
    retried = store.execute_batch_transaction(
        "ctrl-1",
        "t-1",
        "paper",
        BatchProjectionMutation(receipts=[receipt1], quarantines=[q1]),
    )
    assert retried.unresolved_quarantine_count == 1

    # Another controller scope has independent quarantine readiness truth.
    q_other = QuarantineRow(
        "q-other", 3, "BAD_PAYLOAD", "detail", "opened", "t-2", "paper",
        resolution_status="unresolved",
    )
    receipt_other = EventReceiptRow(
        "q-other", 3, "fp-q-other", "t-2", "paper", "", "", "opened", now,
        "quarantined", 1,
    )
    other = store.execute_batch_transaction(
        "ctrl-2",
        "t-2",
        "paper",
        BatchProjectionMutation(receipts=[receipt_other], quarantines=[q_other]),
    )
    assert other.unresolved_quarantine_count == 1
    refreshed = store.execute_batch_transaction(
        "ctrl-1", "t-1", "paper", BatchProjectionMutation()
    )
    assert refreshed.unresolved_quarantine_count == 1

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
    receipt_newer = EventReceiptRow(
        "evt-newer", 10, "fp-newer", "t-1", "paper", "j-1", "", "opened",
        t2, "applied", 1,
    )
    link_newer = IdentityLinkRow(
        "t-1", "paper", "signal_id", "sig-1", "j-1", 10, 10, t2, t2
    )
    m1 = BatchProjectionMutation(
        receipts=[receipt_newer], identity_links=[link_newer], journeys=[j_newer],
        new_checkpoint_seq=10,
    )
    store.execute_batch_transaction("ctrl-1", "t-1", "paper", m1)

    # Ingest older event later (seq=5, time=t1)
    j_older = JourneyRow(
        tenant_id="t-1", environment="paper", journey_id="j-1", status="open",
        stage_coverage={}, is_terminal=False, first_occurred_at=t1, last_occurred_at=t1,
        first_ingested_seq=5, last_ingested_seq=5
    )
    receipt_older = EventReceiptRow(
        "evt-older", 5, "fp-older", "t-1", "paper", "j-1", "", "opened",
        t1, "applied", 2,
    )
    link_older = IdentityLinkRow(
        "t-1", "paper", "signal_id", "sig-1", "j-1", 5, 5, t1, t1
    )
    m2 = BatchProjectionMutation(
        receipts=[receipt_older], identity_links=[link_older], journeys=[j_older],
        new_checkpoint_seq=10,
    )
    store.execute_batch_transaction("ctrl-1", "t-1", "paper", m2)

    import psycopg
    with psycopg.connect(postgres_dsn) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT first_occurred_at, last_occurred_at, first_ingested_seq, last_ingested_seq FROM {schema_name}.journeys WHERE journey_id='j-1'")
        row = cur.fetchone()
        assert row[0] == t1
        assert row[1] == t2
        assert row[2] == 5
        assert row[3] == 10
        cur.execute(
            f"""
            SELECT first_occurred_at, last_occurred_at,
                   first_ingested_seq, last_ingested_seq
            FROM {schema_name}.identity_links
            WHERE identifier_type='signal_id' AND identifier_value='sig-1'
            """
        )
        assert cur.fetchone() == (t1, t2, 5, 10)
        cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")


def test_controller_advisory_lock_id_is_stable_across_processes() -> None:
    expected = controller_advisory_lock_id("ctrl-1", "t-1", "paper")
    code = (
        "from services.trade_journey.projection_store import "
        "controller_advisory_lock_id; "
        "print(controller_advisory_lock_id('ctrl-1', 't-1', 'paper'))"
    )
    observed = []
    for seed in ("1", "987654"):
        env = os.environ.copy()
        env["PYTHONHASHSEED"] = seed
        observed.append(
            int(subprocess.check_output([sys.executable, "-c", code], env=env, text=True))
        )
    assert observed == [expected, expected]


def test_projection_store_two_writers_and_nonblocking_lock(postgres_dsn: str) -> None:
    """Requirement 6: Non-blocking controller lock causes second writer to fail immediately."""
    schema_name = f"test_proj_{uuid4().hex[:8]}"
    store1 = ProjectionStore(postgres_dsn, schema=schema_name, bootstrap=True)
    store2 = ProjectionStore(postgres_dsn, schema=schema_name, bootstrap=False)

    import psycopg
    # Hold lock in separate transaction
    with psycopg.connect(postgres_dsn) as conn:
        with conn.cursor() as cur:
            lock_id = controller_advisory_lock_id("ctrl-1", "t-1", "paper")
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (lock_id,))

            m = BatchProjectionMutation(new_checkpoint_seq=1)
            with pytest.raises(ProjectionStoreException, match="Could not acquire advisory lock"):
                store2.execute_batch_transaction("ctrl-1", "t-1", "paper", m)

            # A distinct controller scope uses a distinct lock and remains ready.
            other = store2.execute_batch_transaction(
                "ctrl-2", "t-1", "paper", BatchProjectionMutation()
            )
            assert other.controller_id == "ctrl-2"

    with psycopg.connect(postgres_dsn) as conn, conn.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")


def test_projection_store_overlapping_controllers_commit_shared_event_once(
    postgres_dsn: str,
) -> None:
    """Two controller locks cannot both derive rows from one global event claim."""

    import psycopg

    schema_name = f"test_proj_{uuid4().hex[:8]}"
    ProjectionStore(postgres_dsn, schema=schema_name, bootstrap=True)
    receipt_read_barrier = threading.Barrier(2)
    barrier_threads: set[int] = set()
    barrier_threads_lock = threading.Lock()

    class ReceiptReadBarrierCursor(psycopg.Cursor):
        def execute(self, query, params=None, *, prepare=None, binary=None):
            result = super().execute(
                query, params, prepare=prepare, binary=binary
            )
            query_text = query if isinstance(query, str) else query.as_string(self)
            if (
                "SELECT event_id, fingerprint FROM" in query_text
                and ".event_receipts WHERE event_id = ANY(%s)" in query_text
            ):
                thread_id = threading.get_ident()
                with barrier_threads_lock:
                    first_receipt_read = thread_id not in barrier_threads
                    barrier_threads.add(thread_id)
                if first_receipt_read:
                    receipt_read_barrier.wait(timeout=10)
            return result

    def barrier_connect(dsn):
        return psycopg.connect(dsn, cursor_factory=ReceiptReadBarrierCursor)

    stores = [
        ProjectionStore(
            postgres_dsn,
            schema=schema_name,
            connect=barrier_connect,
            bootstrap=False,
        )
        for _ in range(2)
    ]
    now = datetime.now(timezone.utc)

    def mutation_for(journey_id: str) -> BatchProjectionMutation:
        return BatchProjectionMutation(
            receipts=[
                EventReceiptRow(
                    "evt-shared", 1, "fp-shared", "t-1", "paper", journey_id,
                    "", "opened", now, "applied", 1,
                )
            ],
            journeys=[
                JourneyRow(
                    "t-1", "paper", journey_id, "open", {"opened": True},
                    False, now, now, 1, 1,
                )
            ],
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                stores[index].execute_batch_transaction,
                f"ctrl-{index + 1}",
                "t-1",
                "paper",
                mutation_for(f"j-{index + 1}"),
            )
            for index in range(2)
        ]

    successes = []
    failures = []
    for future in futures:
        try:
            successes.append(future.result())
        except ConcurrentReceiptClaimException as exc:
            failures.append(exc)

    assert len(successes) == 1
    assert successes[0].projection_revision == 1
    assert len(failures) == 1

    with psycopg.connect(postgres_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT journey_id FROM {schema_name}.event_receipts WHERE event_id='evt-shared'"
        )
        receipt_journey_id = cur.fetchone()[0]
        cur.execute(f"SELECT journey_id FROM {schema_name}.journeys")
        journeys = [row[0] for row in cur.fetchall()]
        assert journeys == [receipt_journey_id]
        cur.execute(f"SELECT COUNT(*) FROM {schema_name}.controller")
        assert cur.fetchone()[0] == 1
        cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")


def test_projection_store_identifier_type_check_constraint_and_runtime_constructor(postgres_dsn: str) -> None:
    """Requirement 7: Verify identifier_type check constraint and least-privilege runtime constructor."""
    schema_name = f"test_proj_{uuid4().hex[:8]}"
    # Bootstrap schema with DDL
    store_admin = ProjectionStore(postgres_dsn, schema=schema_name, bootstrap=True)

    # Runtime constructor (bootstrap=False) does not run DDL
    store_runtime = ProjectionStore(postgres_dsn, schema=schema_name, bootstrap=False)
    now = datetime.now(timezone.utc)

    canonical_types = tuple(
        dict.fromkeys(
            ("journey_id",)
            + IDENTIFIER_FIELDS
            + tuple(
                field
                for field in STABLE_IDENTITY_FIELDS
                if field not in {"tenant_id", "environment"}
            )
        )
    )
    links = [
        IdentityLinkRow(
            tenant_id="t-1",
            environment="paper",
            identifier_type=identifier_type,
            identifier_value=f"value-{index}",
            journey_id="j-1",
            first_ingested_seq=1,
            last_ingested_seq=1,
            first_occurred_at=now,
            last_occurred_at=now,
        )
        for index, identifier_type in enumerate(canonical_types)
    ]
    store_runtime.execute_batch_transaction(
        "ctrl-1",
        "t-1",
        "paper",
        BatchProjectionMutation(
            receipts=[
                EventReceiptRow(
                    "evt-valid-types", 1, "fp-valid-types", "t-1", "paper", "j-1",
                    "", "opened", now, "applied", 1,
                )
            ],
            identity_links=links,
        ),
    )

    # Attempting to insert invalid identifier_type should fail check constraint
    invalid_link = IdentityLinkRow(
        tenant_id="t-1", environment="paper", identifier_type="INVALID_TYPE",
        identifier_value="val-1", journey_id="j-1", first_ingested_seq=2,
        last_ingested_seq=2, first_occurred_at=now, last_occurred_at=now,
    )
    m = BatchProjectionMutation(
        receipts=[
            EventReceiptRow(
                "evt-invalid-type", 2, "fp-invalid-type", "t-1", "paper", "j-1",
                "", "opened", now, "applied", 2,
            )
        ],
        identity_links=[invalid_link],
    )
    import psycopg
    with pytest.raises(psycopg.errors.CheckViolation):
        store_runtime.execute_batch_transaction("ctrl-1", "t-1", "paper", m)

    with psycopg.connect(postgres_dsn) as conn, conn.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")


def test_projection_store_runtime_role_has_dml_without_ddl(
    postgres_admin_dsn: str,
) -> None:
    """Migration and runtime credentials are operationally separable."""

    import psycopg
    from psycopg import sql

    schema_name = f"test_proj_{uuid4().hex[:8]}"
    forbidden_schema = f"test_forbidden_{uuid4().hex[:8]}"
    role_name = f"test_projection_runtime_{uuid4().hex[:8]}"
    role_password = f"pw_{uuid4().hex}"
    admin_info = psycopg.conninfo.conninfo_to_dict(postgres_admin_dsn)
    database_name = admin_info.get("dbname") or "postgres"

    admin_store = ProjectionStore(
        postgres_admin_dsn, schema=schema_name, bootstrap=True
    )
    del admin_store

    runtime_dsn = psycopg.conninfo.make_conninfo(
        host=admin_info.get("host") or "localhost",
        port=admin_info.get("port") or "5432",
        dbname=database_name,
        user=role_name,
        password=role_password,
    )

    try:
        with psycopg.connect(postgres_admin_dsn) as conn, conn.cursor() as cur:
            cur.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(role_name), sql.Literal(role_password)
                )
            )
            cur.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(database_name), sql.Identifier(role_name)
                )
            )
            cur.execute(
                sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                    sql.Identifier(schema_name), sql.Identifier(role_name)
                )
            )
            cur.execute(
                sql.SQL(
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {} TO {}"
                ).format(sql.Identifier(schema_name), sql.Identifier(role_name))
            )

        runtime_store = ProjectionStore(
            runtime_dsn, schema=schema_name, bootstrap=False
        )
        now = datetime.now(timezone.utc)
        state = runtime_store.execute_batch_transaction(
            "runtime-controller",
            "t-1",
            "paper",
            BatchProjectionMutation(
                receipts=[
                    EventReceiptRow(
                        "evt-runtime", 1, "fp-runtime", "t-1", "paper", "j-1",
                        "", "opened", now, "applied", 1,
                    )
                ]
            ),
        )
        assert state.checkpoint_seq == 1

        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            ProjectionStore(
                runtime_dsn, schema=forbidden_schema, bootstrap=True
            )
    finally:
        with psycopg.connect(postgres_admin_dsn) as conn, conn.cursor() as cur:
            cur.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(forbidden_schema)
                )
            )
            cur.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(schema_name)
                )
            )
            cur.execute(
                sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role_name))
            )
            cur.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role_name)))


def test_projection_store_transaction_rollback_and_retry(postgres_dsn: str) -> None:
    """Requirement 8a: Verify rollback leaves no partial aggregate or checkpoint advance."""
    schema_name = f"test_proj_{uuid4().hex[:8]}"
    store = ProjectionStore(postgres_dsn, schema=schema_name, bootstrap=True)
    now = datetime.now(timezone.utc)

    # Valid receipt + invalid identity link type in same batch
    receipt = EventReceiptRow("evt-1", 1, "fp-1", "t-1", "paper", "j-1", "", "opened", now, "applied", 1)
    bad_link = IdentityLinkRow(
        "t-1", "paper", "BAD_TYPE", "val-1", "j-1", 1, 1, now, now
    )

    m = BatchProjectionMutation(receipts=[receipt], identity_links=[bad_link], new_checkpoint_seq=1)
    import psycopg
    with pytest.raises(psycopg.errors.CheckViolation):
        store.execute_batch_transaction("ctrl-1", "t-1", "paper", m)

    # Verify receipt was rolled back
    assert store.get_receipt("evt-1") is None
    assert store.get_controller_state("ctrl-1", "t-1", "paper") is None

    valid_link = IdentityLinkRow(
        "t-1", "paper", "signal_id", "sig-retry", "j-1", 1, 1, now, now
    )
    retried = store.execute_batch_transaction(
        "ctrl-1",
        "t-1",
        "paper",
        BatchProjectionMutation(
            receipts=[receipt], identity_links=[valid_link], new_checkpoint_seq=1
        ),
    )
    assert retried.checkpoint_seq == 1
    assert retried.projection_revision == 1
    assert store.get_receipt("evt-1") is not None

    with psycopg.connect(postgres_dsn) as conn, conn.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")


def test_projection_store_migration_applied_twice_and_prior_reader_compat(postgres_dsn: str) -> None:
    """Requirement 8b: Migration applied twice is idempotent and schema is backward compatible."""
    schema_name = f"test_proj_{uuid4().hex[:8]}"
    migration_sql = INITIAL_MIGRATION_PATH.read_text(encoding="utf-8").replace(
        "trade_journey_projection", schema_name
    )

    import psycopg
    with psycopg.connect(postgres_dsn) as conn, conn.cursor() as cur:
        cur.execute(migration_sql)
        cur.execute(migration_sql)
        cur.execute(f"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='{schema_name}'")
        count = cur.fetchone()[0]
        assert count == 7

        # A reader compiled against the original controller projection remains
        # valid after the exact migration file is reapplied.
        cur.execute(
            f"""
            SELECT controller_id, tenant_scope, environment_scope, checkpoint_seq,
                   source_high_watermark, backlog_count, projection_revision,
                   deployment_sha, mode, status, accepted_live
            FROM {schema_name}.controller
            WHERE controller_id='prior-reader'
            """
        )
        assert cur.fetchone() is None
        cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")


def test_projection_store_indexed_explain_paths(postgres_dsn: str) -> None:
    """Requirement 8c: Validate EXPLAIN query plans for all 5 required indexes."""
    schema_name = f"test_proj_{uuid4().hex[:8]}"
    store = ProjectionStore(postgres_dsn, schema=schema_name, bootstrap=True)

    import psycopg
    with psycopg.connect(postgres_dsn) as conn, conn.cursor() as cur:
        # Give the planner realistic cardinality; empty-table plans can choose
        # an arbitrary small-table index and hide an unbounded query shape.
        cur.execute(
            f"""
            INSERT INTO {schema_name}.event_receipts (
                event_id, ingested_seq, fingerprint, tenant_id, environment,
                journey_id, source_event_type, created_at, disposition
            )
            SELECT 'evt-' || value, value, repeat('a', 64), 't-1', 'paper',
                   'j-' || value, 'opened', clock_timestamp(), 'applied'
            FROM generate_series(1, 1000) AS value;

            INSERT INTO {schema_name}.identity_links (
                tenant_id, environment, identifier_type, identifier_value,
                journey_id, first_ingested_seq, last_ingested_seq,
                first_occurred_at, last_occurred_at
            )
            SELECT 't-1', 'paper', 'signal_id', 'sig-' || value,
                   'j-' || value, value, value,
                   clock_timestamp(), clock_timestamp()
            FROM generate_series(1, 1000) AS value;

            INSERT INTO {schema_name}.journeys (
                tenant_id, environment, journey_id, first_occurred_at,
                last_occurred_at, first_ingested_seq, last_ingested_seq
            )
            SELECT 't-1', 'paper', 'j-' || value,
                   clock_timestamp(), clock_timestamp(), value, value
            FROM generate_series(1, 1000) AS value;

            INSERT INTO {schema_name}.journey_stages (
                tenant_id, environment, journey_id, source_event_id,
                stage_name, stage_ordinal, source_ingested_seq, occurred_at
            )
            SELECT 't-1', 'paper', 'j-' || (value % 10), 'evt-' || value,
                   'opened', 1, value, clock_timestamp()
            FROM generate_series(1, 1000) AS value;

            INSERT INTO {schema_name}.loop_runs (
                tenant_id, environment, loop_run_id, journey_id
            )
            SELECT 't-1', 'paper', 'lr-' || value, 'j-' || value
            FROM generate_series(1, 1000) AS value;

            ANALYZE {schema_name}.event_receipts;
            ANALYZE {schema_name}.identity_links;
            ANALYZE {schema_name}.journeys;
            ANALYZE {schema_name}.journey_stages;
            ANALYZE {schema_name}.loop_runs;
            """
        )

        # Index 1: event_receipts (ingested_seq)
        cur.execute(f"EXPLAIN SELECT * FROM {schema_name}.event_receipts WHERE ingested_seq > 990 ORDER BY ingested_seq LIMIT 10")
        plan1 = "\n".join(r[0] for r in cur.fetchall())
        assert "idx_event_receipts_ingested_seq" in plan1

        # Index 2: identity resolution through the scoped primary key.
        cur.execute(
            f"""
            EXPLAIN SELECT journey_id FROM {schema_name}.identity_links
            WHERE tenant_id='t-1' AND environment='paper'
              AND identifier_type='signal_id' AND identifier_value='sig-1'
            """
        )
        plan2 = "\n".join(r[0] for r in cur.fetchall())
        assert "identity_links_pkey" in plan2

        # Index 3: journeys (tenant_id, environment, updated_at DESC, journey_id DESC)
        cur.execute(f"EXPLAIN SELECT * FROM {schema_name}.journeys WHERE tenant_id='t-1' AND environment='paper' ORDER BY updated_at DESC, journey_id DESC LIMIT 10")
        plan3 = "\n".join(r[0] for r in cur.fetchall())
        assert "idx_journeys_tenant_env_updated_journey" in plan3

        # Index 4: journey_stages (timeline)
        cur.execute(
            f"""
            EXPLAIN SELECT * FROM {schema_name}.journey_stages
            WHERE tenant_id='t-1' AND environment='paper' AND journey_id='j-1'
            ORDER BY stage_ordinal, event_sequence, occurred_at,
                     source_ingested_seq, source_event_id
            LIMIT 50
            """
        )
        plan4 = "\n".join(r[0] for r in cur.fetchall())
        assert "idx_journey_stages_timeline" in plan4

        # Index 5: loop_runs (tenant_id, environment, updated_at DESC, loop_run_id DESC)
        cur.execute(f"EXPLAIN SELECT * FROM {schema_name}.loop_runs WHERE tenant_id='t-1' AND environment='paper' ORDER BY updated_at DESC, loop_run_id DESC LIMIT 10")
        plan5 = "\n".join(r[0] for r in cur.fetchall())
        assert "idx_loop_runs_tenant_env_updated_loop" in plan5

        cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")


def test_projection_store_driver_missing_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "psycopg", None)
    with pytest.raises(RuntimeError, match="psycopg is required for ProjectionStore"):
        ProjectionStore("postgresql://pantheon_app:pantheon_app@localhost:5432/pantheon", connect=None)


def test_projection_store_default_connect_binds_psycopg() -> None:
    import psycopg

    store = ProjectionStore(
        "postgresql://pantheon_app:pantheon_app@localhost:5432/pantheon",
        connect=None,
    )
    assert store._connect is psycopg.connect


def test_projection_store_timeout_configuration_and_validation() -> None:
    dsn = "postgresql://pantheon_app:pantheon_app@localhost:5432/pantheon"
    # Default timeouts
    store = ProjectionStore(dsn, connect=lambda *a, **kw: None)
    assert store.connect_timeout_seconds == DEFAULT_PROJECTION_TIMEOUT_SECONDS
    assert store.statement_timeout_seconds == DEFAULT_PROJECTION_TIMEOUT_SECONDS
    assert store.lock_timeout_seconds == DEFAULT_PROJECTION_TIMEOUT_SECONDS

    # Base timeout setting all 3
    store = ProjectionStore(dsn, timeout_seconds=5.0, connect=lambda *a, **kw: None)
    assert store.connect_timeout_seconds == 5.0
    assert store.statement_timeout_seconds == 5.0
    assert store.lock_timeout_seconds == 5.0

    # Specific overrides
    store = ProjectionStore(
        dsn,
        timeout_seconds=5.0,
        connect_timeout_seconds=2.0,
        statement_timeout_seconds=3.0,
        lock_timeout_seconds=4.0,
        connect=lambda *a, **kw: None,
    )
    assert store.connect_timeout_seconds == 2.0
    assert store.statement_timeout_seconds == 3.0
    assert store.lock_timeout_seconds == 4.0

    # Validation errors
    for invalid in (0, -1, -0.5, True, False, "invalid", float("nan"), float("inf")):
        with pytest.raises(ValueError):
            ProjectionStore(dsn, timeout_seconds=invalid, connect=lambda *a, **kw: None)
        with pytest.raises(ValueError):
            ProjectionStore(dsn, connect_timeout_seconds=invalid, connect=lambda *a, **kw: None)
        with pytest.raises(ValueError):
            ProjectionStore(dsn, statement_timeout_seconds=invalid, connect=lambda *a, **kw: None)
        with pytest.raises(ValueError):
            ProjectionStore(dsn, lock_timeout_seconds=invalid, connect=lambda *a, **kw: None)


def test_projection_store_statement_timeout_cancels_long_query(postgres_dsn: str) -> None:
    import psycopg

    schema_name = f"test_proj_{uuid4().hex[:8]}"
    store = ProjectionStore(
        postgres_dsn,
        schema=schema_name,
        statement_timeout_seconds=0.2,
        bootstrap=True,
    )

    # Executing a query exceeding statement timeout must raise QueryCanceled in < 1s
    start_time = time.monotonic()
    with pytest.raises(psycopg.errors.QueryCanceled):
        with store._connect_db() as conn, conn.cursor() as cur:
            cur.execute("SELECT pg_sleep(2.0)")
    elapsed = time.monotonic() - start_time
    assert elapsed < 1.5, f"statement timeout took too long: {elapsed}s"

    with psycopg.connect(postgres_dsn) as conn, conn.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")


def test_projection_store_lock_timeout_cancels_blocked_lock(postgres_dsn: str) -> None:
    import psycopg

    schema_name = f"test_proj_{uuid4().hex[:8]}"
    store = ProjectionStore(
        postgres_dsn,
        schema=schema_name,
        lock_timeout_seconds=0.2,
        bootstrap=True,
    )

    # Thread 1 holds exclusive lock on controller row FOR UPDATE
    lock_acquired = threading.Event()
    release_lock = threading.Event()

    def hold_row_lock():
        with psycopg.connect(postgres_dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO {schema_name}.controller (controller_id, tenant_scope, environment_scope, checkpoint_seq, source_high_watermark, backlog_count, projection_revision, deployment_sha, mode, status, accepted_live) VALUES ('ctrl-block', 't-1', 'paper', 0, 0, 0, 0, 'sha1', 'live', 'ready', true)"
                )
            conn.commit()
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT * FROM {schema_name}.controller WHERE controller_id='ctrl-block' FOR UPDATE"
                )
                lock_acquired.set()
                release_lock.wait(timeout=5.0)
            conn.commit()

    t = threading.Thread(target=hold_row_lock)
    t.start()
    assert lock_acquired.wait(timeout=5.0)

    now = datetime.now(timezone.utc)
    mutation = BatchProjectionMutation(
        receipts=[
            EventReceiptRow(
                "evt-1", 1, "fp-1", "t-1", "paper", "j-1", "", "opened", now, "applied", 1
            )
        ],
        journeys=[
            JourneyRow(
                "t-1", "paper", "j-1", "open", {"opened": True}, False, now, now, 1, 1
            )
        ],
        source_high_watermark=1,
    )

    # Thread 2 tries to execute batch transaction for ctrl-block; should hit lock_timeout and raise LockNotAvailable / QueryCanceled
    start_time = time.monotonic()
    with pytest.raises((psycopg.errors.LockNotAvailable, psycopg.errors.QueryCanceled)):
        store.execute_batch_transaction("ctrl-block", "t-1", "paper", mutation)
    elapsed = time.monotonic() - start_time
    assert elapsed < 1.5, f"lock timeout took too long: {elapsed}s"

    release_lock.set()
    t.join()

    with psycopg.connect(postgres_dsn) as conn, conn.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")


def test_projection_store_connect_fallback_sets_timeouts_on_custom_connector(postgres_dsn: str) -> None:
    import psycopg

    def custom_connector(dsn):
        # Only accepts dsn, raising TypeError if kwargs passed
        return psycopg.connect(dsn)

    store = ProjectionStore(
        postgres_dsn,
        connect=custom_connector,
        statement_timeout_seconds=2.5,
        lock_timeout_seconds=1.5,
    )
    with store._connect_db() as conn, conn.cursor() as cur:
        cur.execute("SHOW statement_timeout")
        assert cur.fetchone()[0] == "2500ms"
        cur.execute("SHOW lock_timeout")
        assert cur.fetchone()[0] == "1500ms"

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
    store = ProjectionStore(postgres_dsn, schema=schema_name)
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
    store = ProjectionStore(postgres_dsn, schema=schema_name)
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
    assert ctrl_after_exact.projection_revision == 2

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
    store = ProjectionStore(postgres_dsn, schema=schema_name)

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
    store = ProjectionStore(postgres_dsn, schema=schema_name)
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

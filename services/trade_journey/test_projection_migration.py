"""Tests for LIFECYCLE-PROJ-MIGRATE-001 backfill and parity tooling."""

from __future__ import annotations

import copy
import json
import os
import random
from pathlib import Path
from typing import Any

import pytest

from services.trade_journey.incremental_materializer import IncrementalLifecycleMaterializer
from services.trade_journey.lifecycle_projector import ConflictingLifecycleEvent
from services.trade_journey.projection_migration import (
    BackfillCoordinator,
    build_batch_mutation,
    compare_category,
    legacy_identity_rows,
    legacy_journey_rows,
    legacy_loop_rows,
    legacy_quarantine_rows,
    legacy_stage_rows,
    migration_controller_id,
    projection_identity_rows,
    projection_journey_rows,
    projection_loop_rows,
    projection_quarantine_rows,
    projection_stage_rows,
    reduce_source_rows,
    stable_hash,
    summarize_parity,
)
from services.trade_journey.test_lifecycle_projector import lifecycle_rows


# ---------------------------------------------------------------------------
# migration_controller_id
# ---------------------------------------------------------------------------


def test_migration_controller_id_is_distinct_from_the_live_controller():
    assert migration_controller_id("ctrl-1") == "ctrl-1-migrate"


def test_migration_controller_id_rejects_an_already_scoped_id():
    with pytest.raises(ValueError):
        migration_controller_id("ctrl-1-migrate")


# ---------------------------------------------------------------------------
# reduce_source_rows
# ---------------------------------------------------------------------------


def test_reduce_source_rows_accepts_full_lifecycle_and_reports_watermark():
    rows = lifecycle_rows()
    reduced = reduce_source_rows(rows, mode="backfill")

    assert len(reduced.entries) == len(rows)
    assert reduced.quarantine == ()
    assert reduced.ignored == 0
    assert reduced.high_watermark == max(row["ingested_seq"] for row in rows)
    assert all(entry["source_mode"] == "backfill" for entry in reduced.entries)
    assert all(entry["accepted_live"] is False for entry in reduced.entries)


def test_reduce_source_rows_rejects_live_mode():
    with pytest.raises(ValueError):
        reduce_source_rows(lifecycle_rows(), mode="live")


def test_reduce_source_rows_ignores_non_lifecycle_event_types():
    rows = lifecycle_rows()
    rows[0]["event_type"] = "some_unrelated_telemetry_event"
    rows[0]["payload"]["event_type"] = "some_unrelated_telemetry_event"
    reduced = reduce_source_rows(rows, mode="backfill")
    assert reduced.ignored == 1
    assert len(reduced.entries) == len(rows) - 1


def test_reduce_source_rows_quarantines_invalid_identity_and_progresses():
    rows = lifecycle_rows()
    rows[0]["payload"]["correlation_envelope"] = {}
    reduced = reduce_source_rows(rows, mode="backfill")

    assert len(reduced.quarantine) == 1
    quarantined = reduced.quarantine[0]
    assert quarantined["event_id"] == rows[0]["event_id"]
    assert quarantined["ingested_seq"] == rows[0]["ingested_seq"]
    assert "correlation_envelope" in quarantined["reason"]
    # The remaining rows in the batch are still accepted; one bad row does
    # not block the batch, and the watermark still advances past it.
    assert len(reduced.entries) == len(rows) - 1
    assert reduced.high_watermark == max(row["ingested_seq"] for row in rows)


def test_reduce_source_rows_conflicting_duplicate_raises_when_staged():
    rows = lifecycle_rows()
    conflicting = copy.deepcopy(rows[0])
    conflicting["payload"]["metrics"] = {"action": "mutated-for-conflict-test"}
    reduced = reduce_source_rows([rows[0], conflicting], mode="backfill")
    assert len(reduced.entries) == 2

    materializer = IncrementalLifecycleMaterializer()
    with pytest.raises(ConflictingLifecycleEvent):
        materializer.stage_batch(reduced.entries)


# ---------------------------------------------------------------------------
# build_batch_mutation: mapping bounded aggregates -> relational rows
# ---------------------------------------------------------------------------


def _staged_from_rows(rows: list[dict[str, Any]]):
    reduced = reduce_source_rows(rows, mode="backfill")
    materializer = IncrementalLifecycleMaterializer()
    staged, affected, accepted, duplicates = materializer.stage_batch(reduced.entries)
    return reduced, materializer, staged, affected, accepted, duplicates


def test_build_batch_mutation_derives_receipts_stages_journey_and_loop_rows():
    rows = lifecycle_rows()
    reduced, _materializer, staged, affected, accepted, duplicates = _staged_from_rows(rows)
    assert accepted == len(rows)
    assert duplicates == 0
    assert len(affected) == 1

    mutation = build_batch_mutation(
        staged,
        reduced,
        projection_revision=1,
        source_high_watermark=reduced.high_watermark,
        backlog_count=0,
    )

    assert mutation.mode == "backfill"
    assert mutation.accepted_live is False
    assert len(mutation.stages) == len(rows)
    assert len(mutation.journeys) == 1
    assert len(mutation.loop_runs) == 1
    assert len(mutation.receipts) == len(rows)
    assert all(receipt.disposition == "applied" for receipt in mutation.receipts)
    assert mutation.journeys[0].journey_id == "tj-paper-001"
    assert mutation.journeys[0].status == "completed"
    assert mutation.loop_runs[0].journey_id == "tj-paper-001"


def test_build_batch_mutation_includes_quarantine_receipt_and_row():
    rows = lifecycle_rows()
    rows[0]["payload"]["correlation_envelope"] = {}
    reduced, _materializer, staged, _affected, _accepted, _duplicates = _staged_from_rows(rows)

    mutation = build_batch_mutation(
        staged, reduced, projection_revision=1, source_high_watermark=reduced.high_watermark, backlog_count=0,
    )

    quarantine_receipts = [r for r in mutation.receipts if r.disposition == "quarantined"]
    assert len(quarantine_receipts) == 1
    assert quarantine_receipts[0].event_id == rows[0]["event_id"]
    assert len(mutation.quarantines) == 1
    assert mutation.quarantines[0].event_id == rows[0]["event_id"]


def test_build_batch_mutation_duplicate_only_batch_writes_nothing_new():
    rows = lifecycle_rows()
    _reduced1, materializer, staged1, _affected1, _accepted1, _duplicates1 = _staged_from_rows(rows)
    materializer.commit(staged1)

    reduced2 = reduce_source_rows(rows, mode="backfill")
    staged2, affected2, accepted2, duplicates2 = materializer.stage_batch(reduced2.entries)
    assert affected2 == set()
    assert accepted2 == 0
    assert duplicates2 == len(rows)

    mutation2 = build_batch_mutation(
        staged2, reduced2, affected=affected2, projection_revision=2,
        source_high_watermark=reduced2.high_watermark, backlog_count=0,
    )
    assert mutation2.stages == []
    assert mutation2.journeys == []
    assert mutation2.loop_runs == []
    assert mutation2.receipts == []


def test_out_of_order_delivery_converges_to_the_same_journey_row():
    rows = lifecycle_rows()
    shuffled = list(rows)
    random.Random(7).shuffle(shuffled)

    _reduced_a, _materializer_a, staged_a, _affected_a, _accepted_a, _duplicates_a = _staged_from_rows(rows)
    _reduced_b, _materializer_b, staged_b, _affected_b, _accepted_b, _duplicates_b = _staged_from_rows(shuffled)

    # Build via the same mapping helper used in production to compare snapshots.
    from services.trade_journey.projection_migration import _journey_row_for_aggregate

    row_a = _journey_row_for_aggregate(staged_a["tj-paper-001"], projection_revision=1)
    row_b = _journey_row_for_aggregate(staged_b["tj-paper-001"], projection_revision=1)
    assert row_a.status == row_b.status
    assert row_a.stage_coverage == row_b.stage_coverage
    assert row_a.first_ingested_seq == row_b.first_ingested_seq
    assert row_a.last_ingested_seq == row_b.last_ingested_seq


# ---------------------------------------------------------------------------
# BackfillCoordinator: resumable batches over a fake store
# ---------------------------------------------------------------------------


class _FakeControllerRow:
    def __init__(self, checkpoint_seq: int) -> None:
        self.checkpoint_seq = checkpoint_seq


class FakeProjectionStore:
    """Records transactions without requiring a real Postgres connection.

    Exercises exactly the two :class:`ProjectionStore` methods
    :class:`BackfillCoordinator` calls, so the coordinator's resumability and
    batching logic is tested independently of the DB-gated
    ``test_projection_store.py`` suite.
    """

    def __init__(self) -> None:
        self.checkpoint = 0
        self.transactions: list[Any] = []

    def get_controller_state(self, controller_id, tenant_scope, environment_scope):
        if self.checkpoint == 0:
            return None
        return _FakeControllerRow(self.checkpoint)

    def execute_batch_transaction(self, controller_id, tenant_scope, environment_scope, mutation):
        assert controller_id.endswith("-migrate")
        assert mutation.mode == "backfill"
        assert mutation.accepted_live is False
        self.transactions.append(mutation)
        self.checkpoint = mutation.source_high_watermark
        return _FakeControllerRow(self.checkpoint)


def _paged_fetch(rows: list[dict[str, Any]]):
    def fetch(after_seq: int, limit: int) -> list[dict[str, Any]]:
        window = [row for row in rows if row["ingested_seq"] > after_seq]
        window.sort(key=lambda row: row["ingested_seq"])
        return window[:limit]

    return fetch


def test_backfill_coordinator_runs_to_backlog_zero_in_one_pass(tmp_path: Path):
    rows = lifecycle_rows()
    store = FakeProjectionStore()
    coordinator = BackfillCoordinator(
        store,
        controller_id="tj-projector",
        tenant_scope="tenant-a",
        environment_scope="paper",
        fetch_batch=_paged_fetch(rows),
        snapshot_path=tmp_path / "snapshot.json",
        batch_size=500,
    )

    totals = coordinator.run()

    assert totals["batches"] == 1
    assert totals["accepted"] == len(rows)
    assert totals["quarantined"] == 0
    assert totals["checkpoint"] == max(row["ingested_seq"] for row in rows)
    assert len(store.transactions) == 1
    assert len(store.transactions[0].stages) == len(rows)


def test_backfill_coordinator_resumes_from_local_snapshot_across_restarts(tmp_path: Path):
    rows = lifecycle_rows()
    store = FakeProjectionStore()
    snapshot_path = tmp_path / "snapshot.json"

    first = BackfillCoordinator(
        store,
        controller_id="tj-projector",
        tenant_scope="tenant-a",
        environment_scope="paper",
        fetch_batch=_paged_fetch(rows),
        snapshot_path=snapshot_path,
        batch_size=4,
    )
    first_totals = first.run(max_batches=1)
    assert first_totals["batches"] == 1
    assert first_totals["checkpoint"] == 4
    assert snapshot_path.exists()
    snapshot_after_first_run = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert snapshot_after_first_run["checkpoint"] == 4
    assert "tj-paper-001" in snapshot_after_first_run["aggregates"]

    # A brand new coordinator instance (simulating a process restart) resumes
    # from the durable snapshot rather than refetching rows 1-4.
    second = BackfillCoordinator(
        store,
        controller_id="tj-projector",
        tenant_scope="tenant-a",
        environment_scope="paper",
        fetch_batch=_paged_fetch(rows),
        snapshot_path=snapshot_path,
        batch_size=4,
    )
    assert second.checkpoint() == 4
    second_totals = second.run()

    assert second_totals["checkpoint"] == max(row["ingested_seq"] for row in rows)
    assert second_totals["batches"] == 1
    assert second_totals["accepted"] == 4
    # The completed journey row only ever appears once the full lifecycle
    # (rows 5-8) lands; the second run's transaction carries the full stage
    # set for the journey because the snapshot rehydrated the first four.
    final_mutation = store.transactions[-1]
    assert len(final_mutation.journeys) == 1
    assert final_mutation.journeys[0].status == "completed"


def test_backfill_coordinator_resubmitting_an_applied_batch_is_a_no_op(tmp_path: Path):
    rows = lifecycle_rows()
    store = FakeProjectionStore()
    snapshot_path = tmp_path / "snapshot.json"
    coordinator = BackfillCoordinator(
        store,
        controller_id="tj-projector",
        tenant_scope="tenant-a",
        environment_scope="paper",
        fetch_batch=_paged_fetch(rows),
        snapshot_path=snapshot_path,
        batch_size=500,
    )
    coordinator.run()
    assert len(store.transactions) == 1

    # Re-running against the identical fully-applied source window (e.g. an
    # operator re-invoking the CLI) advances zero rows: the snapshot's
    # checkpoint is already at the source high watermark, so the paged fetch
    # returns nothing.
    totals = coordinator.run()
    assert totals["batches"] == 0
    assert len(store.transactions) == 1


# ---------------------------------------------------------------------------
# Deterministic old/new parity
# ---------------------------------------------------------------------------


def test_stable_hash_is_order_independent():
    rows = [{"k": "b", "v": 2}, {"k": "a", "v": 1}]
    reversed_rows = list(reversed(rows))
    assert stable_hash(rows, key_fields=["k"]) == stable_hash(reversed_rows, key_fields=["k"])


def test_stable_hash_changes_with_content():
    rows = [{"k": "a", "v": 1}]
    other = [{"k": "a", "v": 2}]
    assert stable_hash(rows, key_fields=["k"]) != stable_hash(other, key_fields=["k"])


def test_compare_category_matches_when_content_is_equivalent():
    legacy = [{"id": "a", "status": "open"}]
    new = [{"id": "a", "status": "open"}]
    result = compare_category("journey", legacy, new, key_fields=["id"])
    assert result.match is True
    assert result.classification is None


def test_summarize_parity_flags_unexplained_vs_classified_mismatch():
    matching = compare_category("journey", [{"id": "a"}], [{"id": "a"}], key_fields=["id"])
    classified = compare_category(
        "loop", [{"id": "b", "v": 1}], [{"id": "b", "v": 2}], key_fields=["id"], classification="renamed_field"
    )
    unexplained = compare_category("stage", [{"id": "c", "v": 1}], [{"id": "c", "v": 2}], key_fields=["id"])

    summary = summarize_parity([matching, classified, unexplained])

    assert summary["mismatch_count"] == 2
    assert summary["unexplained_mismatch_count"] == 1
    assert summary["categories"]["loop"]["classification"] == "renamed_field"
    assert summary["categories"]["stage"]["classification"] is None


def test_legacy_and_projection_adapters_agree_across_all_categories():
    """End-to-end parity proof: fold real lifecycle rows once, then compare
    the legacy JSON read-model shape against the relational rows this module
    derives from the identical aggregate, across every parity category the
    task declares (controller/backlog is proven by the coordinator tests
    above; duplicate/replay/recovery are proven by the reduction tests
    above)."""
    rows = lifecycle_rows()
    reduced, _materializer, staged, _affected, _accepted, _duplicates = _staged_from_rows(rows)
    mutation = build_batch_mutation(
        staged, reduced, projection_revision=1, source_high_watermark=reduced.high_watermark, backlog_count=0,
    )
    agg = staged["tj-paper-001"]
    legacy_events = agg.journey_events
    legacy_records = {agg.loop_record["loop_run_id"]: agg.loop_record}

    stage_result = compare_category(
        "stage",
        legacy_stage_rows(legacy_events),
        projection_stage_rows(mutation.stages),
        key_fields=["journey_id", "source_event_id", "stage_name"],
    )
    journey_result = compare_category(
        "journey",
        legacy_journey_rows(legacy_events),
        projection_journey_rows(mutation.journeys),
        key_fields=["journey_id"],
    )
    loop_result = compare_category(
        "loop",
        legacy_loop_rows(legacy_records),
        projection_loop_rows(mutation.loop_runs),
        key_fields=["loop_run_id"],
    )
    identity_result = compare_category(
        "identity",
        legacy_identity_rows(legacy_events),
        projection_identity_rows(mutation.identity_links),
        key_fields=["identifier_type", "identifier_value"],
    )
    quarantine_result = compare_category(
        "quarantine",
        legacy_quarantine_rows(reduced.quarantine),
        projection_quarantine_rows(mutation.quarantines),
        key_fields=["event_id"],
    )

    summary = summarize_parity(
        [stage_result, journey_result, loop_result, identity_result, quarantine_result]
    )
    assert summary["unexplained_mismatch_count"] == 0, summary


# ---------------------------------------------------------------------------
# Real-Postgres end-to-end: backfill, restart, and backlog-zero convergence.
#
# Gated exactly like services/trade_journey/test_projection_store.py's own
# suite -- skipped unless TEST_DATABASE_URL points at a real database, since
# ProjectionStore.execute_batch_transaction needs real advisory locks and
# transactional dedup semantics no in-memory fake can stand in for.
# ---------------------------------------------------------------------------


@pytest.fixture
def postgres_dsn() -> str:
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is not set")
    return dsn


def test_backfill_reaches_backlog_zero_and_restart_resumes_against_real_store(
    postgres_dsn: str, tmp_path: Path
):
    from uuid import uuid4

    from services.trade_journey.projection_store import ProjectionStore

    schema_name = f"test_migrate_{uuid4().hex[:8]}"
    store = ProjectionStore(postgres_dsn, schema=schema_name, bootstrap=True)
    try:
        rows = lifecycle_rows()
        snapshot_path = tmp_path / "snapshot.json"

        first = BackfillCoordinator(
            store,
            controller_id="tj-projector-it",
            tenant_scope="tenant-a",
            environment_scope="paper",
            fetch_batch=_paged_fetch(rows),
            snapshot_path=snapshot_path,
            batch_size=4,
        )
        first_totals = first.run(max_batches=1)
        assert first_totals["batches"] == 1
        assert first_totals["checkpoint"] == 4

        # Simulate a process restart: a fresh coordinator instance backed by
        # the same durable store and local snapshot resumes rather than
        # replaying from ingested_seq 0.
        second = BackfillCoordinator(
            store,
            controller_id="tj-projector-it",
            tenant_scope="tenant-a",
            environment_scope="paper",
            fetch_batch=_paged_fetch(rows),
            snapshot_path=snapshot_path,
            batch_size=4,
        )
        second_totals = second.run()
        assert second_totals["checkpoint"] == max(row["ingested_seq"] for row in rows)

        # Backlog zero: re-running against the fully-applied window advances
        # nothing further.
        third = BackfillCoordinator(
            store,
            controller_id="tj-projector-it",
            tenant_scope="tenant-a",
            environment_scope="paper",
            fetch_batch=_paged_fetch(rows),
            snapshot_path=snapshot_path,
            batch_size=4,
        )
        assert third.run()["batches"] == 0

        controller = store.get_controller_state("tj-projector-it-migrate", "tenant-a", "paper")
        assert controller is not None
        assert controller.checkpoint_seq == max(row["ingested_seq"] for row in rows)
        assert controller.mode == "backfill"
        assert controller.accepted_live is False
    finally:
        import psycopg  # type: ignore[import]

        with psycopg.connect(postgres_dsn) as conn, conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")


def test_backfill_duplicate_and_interruption_against_real_store(
    postgres_dsn: str, tmp_path: Path
):
    """Test duplicate event submission, source growth, and multiple backfill/delta interruption points against real Postgres.
    
    Verifies:
    - Resuming from multiple interruption points.
    - Submitting durable duplicate + new events for the same journey/loop.
    - High watermark advancement and backlog zero convergence.
    """
    from uuid import uuid4
    from services.trade_journey.projection_store import ProjectionStore

    schema_name = f"test_migrate_dup_{uuid4().hex[:8]}"
    store = ProjectionStore(postgres_dsn, schema=schema_name, bootstrap=True)
    try:
        base_rows = lifecycle_rows()
        # Create extended source rows: base_rows plus duplicates of base_rows[0..1] with same/higher ingested_seq, plus new event for same journey
        # base_rows has ingested_seq 1..8
        dup_row = copy.deepcopy(base_rows[0])  # duplicate event_id and content
        dup_row["ingested_seq"] = 9
        
        new_event = copy.deepcopy(base_rows[-1])  # new event for same journey/loop
        new_event["event_id"] = "evt-paper-009"
        new_event["ingested_seq"] = 10
        new_event["payload"]["occurred_at"] = "2026-08-01T12:05:00Z"
        
        all_rows = base_rows + [dup_row, new_event]
        snapshot_path = tmp_path / "snapshot.json"

        # Interruption 1: Process batch 1 (size 3) -> rows 1..3
        coord1 = BackfillCoordinator(
            store,
            controller_id="tj-proj-dup-it",
            tenant_scope="tenant-a",
            environment_scope="paper",
            fetch_batch=_paged_fetch(all_rows),
            snapshot_path=snapshot_path,
            batch_size=3,
        )
        res1 = coord1.run(max_batches=1)
        assert res1["batches"] == 1
        assert res1["checkpoint"] == 3

        # Interruption 2: Process batch 2 (size 3) -> rows 4..6
        coord2 = BackfillCoordinator(
            store,
            controller_id="tj-proj-dup-it",
            tenant_scope="tenant-a",
            environment_scope="paper",
            fetch_batch=_paged_fetch(all_rows),
            snapshot_path=snapshot_path,
            batch_size=3,
        )
        res2 = coord2.run(max_batches=1)
        assert res2["batches"] == 1
        assert res2["checkpoint"] == 6

        # Interruption 3: Process batch 3 & remaining -> rows 7..10 (includes duplicate row 9 and new event row 10)
        coord3 = BackfillCoordinator(
            store,
            controller_id="tj-proj-dup-it",
            tenant_scope="tenant-a",
            environment_scope="paper",
            fetch_batch=_paged_fetch(all_rows),
            snapshot_path=snapshot_path,
            batch_size=3,
        )
        res3 = coord3.run()
        assert res3["checkpoint"] == 10

        # Verify against store: controller state updated to 10
        controller = store.get_controller_state("tj-proj-dup-it-migrate", "tenant-a", "paper")
        assert controller is not None
        assert controller.checkpoint_seq == 10

        # Verify duplicate row resulted in no duplicate mutation or error, and backlog zero on next run
        coord4 = BackfillCoordinator(
            store,
            controller_id="tj-proj-dup-it",
            tenant_scope="tenant-a",
            environment_scope="paper",
            fetch_batch=_paged_fetch(all_rows),
            snapshot_path=snapshot_path,
            batch_size=3,
        )
        res4 = coord4.run()
        assert res4["batches"] == 0
        assert res4["checkpoint"] == 10
    finally:
        import psycopg  # type: ignore[import]

        with psycopg.connect(postgres_dsn) as conn, conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")


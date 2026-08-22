"""Tests for LIFECYCLE-PROJ-MIGRATE-001 backfill and parity tooling."""

from __future__ import annotations

import copy
import json
import os
import random
from collections import Counter
from io import StringIO
from pathlib import Path
from typing import Any

import pytest

from services.trade_journey.incremental_materializer import IncrementalLifecycleMaterializer
from services.trade_journey.lifecycle_projector import ConflictingLifecycleEvent
from services.trade_journey.projection_migration import (
    BackfillCoordinator,
    LegacyBundleBackfillCoordinator,
    StreamingMultisetDigest,
    _JsonStream,
    _seek_top_level_member,
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
    read_legacy_bundle_member,
    reduce_source_rows,
    sha256_file,
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
    def __init__(
        self,
        checkpoint_seq: int,
        *,
        mode: str = "backfill",
        status: str = "ready",
        accepted_live: bool = False,
    ) -> None:
        self.checkpoint_seq = checkpoint_seq
        self.mode = mode
        self.status = status
        self.accepted_live = accepted_live


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
        self.adoptions: list[dict[str, Any]] = []

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

    def adopt_legacy_baseline(self, **kwargs):
        self.adoptions.append(dict(kwargs))
        return _FakeControllerRow(
            kwargs["checkpoint_seq"],
            mode="recovery",
            status="repair_only",
            accepted_live=False,
        )


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
# Operator-accepted legacy JSON baseline recovery
# ---------------------------------------------------------------------------


def _legacy_controller_state() -> dict[str, Any]:
    reduced = reduce_source_rows(lifecycle_rows(), mode="backfill")
    materializer = IncrementalLifecycleMaterializer()
    staged, _affected, _accepted, _duplicates = materializer.stage_batch(
        reduced.entries
    )
    materializer.commit(staged)
    return {
        "aggregates": materializer.serialize_aggregates(),
        "controller": {
            "controller_id": "canonical-lifecycle-projector",
            "checkpoint": reduced.high_watermark,
            "backlog": 0,
            "quarantine_count": 0,
            "accepted_live": True,
            "last_error": None,
            "deployment_sha": "a" * 40,
        },
        "schema_version": "pantheon.lifecycle-projector-state.v1",
    }


def test_json_stream_skips_large_members_with_tiny_chunks():
    payload = json.dumps(
        {"large": {"nested": ["quoted } value", {"x": "y" * 100}]}, "wanted": {"ok": True}},
        sort_keys=True,
    )
    reader = _JsonStream(StringIO(payload), chunk_size=7)

    assert _seek_top_level_member(reader, "wanted") is True
    assert reader.read_value() == {"ok": True}


def test_legacy_bundle_backfill_streams_exact_baseline_and_seeds_recovery(tmp_path: Path):
    source = tmp_path / "controller_state.json"
    source.write_text(
        json.dumps(_legacy_controller_state(), sort_keys=True), encoding="utf-8"
    )
    store = FakeProjectionStore()
    snapshot = tmp_path / "legacy.snapshot.json"
    coordinator = LegacyBundleBackfillCoordinator(
        store,
        controller_id="canonical-lifecycle-projector",
        tenant_scope="*",
        environment_scope="*",
        controller_state_path=source,
        expected_sha256=sha256_file(source),
        snapshot_path=snapshot,
        accepted_checkpoint=8,
        accepted_controller_deployment_sha="a" * 40,
        deployment_sha="b" * 40,
        batch_size=1,
    )

    result = coordinator.run()

    assert result["aggregates"] == 1
    assert result["receipts"] == 8
    assert result["journeys"] == 1
    assert result["loop_runs"] == 1
    assert result["stages"] == 8
    assert result["checkpoint"] == 8
    assert result["import_complete"] is True
    assert result["live_controller_seeded"] is True
    assert result["live_controller_mode"] == "recovery"
    assert result["live_controller_status"] == "repair_only"
    assert result["accepted_live"] is False
    assert len(store.transactions) == 1
    assert store.transactions[0].mode == "backfill"
    assert store.transactions[0].accepted_live is False
    assert len(store.adoptions) == 1
    assert store.adoptions[0]["expected_receipts"] == 8
    persisted = json.loads(snapshot.read_text(encoding="utf-8"))
    assert persisted["import_complete"] is True
    assert persisted["live_controller_seeded"] is True
    assert read_legacy_bundle_member(source, "controller")["checkpoint"] == 8

    rerun = coordinator.run()
    assert rerun["batches"] == 1
    assert len(store.transactions) == 1
    assert len(store.adoptions) == 2


def test_legacy_bundle_backfill_rejects_unreviewed_checksum(tmp_path: Path):
    source = tmp_path / "controller_state.json"
    source.write_text(
        json.dumps(_legacy_controller_state(), sort_keys=True), encoding="utf-8"
    )
    store = FakeProjectionStore()
    coordinator = LegacyBundleBackfillCoordinator(
        store,
        controller_id="canonical-lifecycle-projector",
        tenant_scope="*",
        environment_scope="*",
        controller_state_path=source,
        expected_sha256="0" * 64,
        snapshot_path=tmp_path / "legacy.snapshot.json",
        accepted_checkpoint=8,
    )

    with pytest.raises(ValueError, match="checksum"):
        coordinator.run()
    assert store.transactions == []
    assert store.adoptions == []


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


def test_streaming_multiset_digest_is_order_independent_and_duplicate_sensitive():
    first = StreamingMultisetDigest()
    second = StreamingMultisetDigest()
    duplicate = StreamingMultisetDigest()
    for row in ({"k": "a", "v": 1}, {"k": "b", "v": 2}):
        first.update(row)
    for row in ({"k": "b", "v": 2}, {"k": "a", "v": 1}):
        second.update(row)
    for row in ({"k": "a", "v": 1}, {"k": "b", "v": 2}, {"k": "b", "v": 2}):
        duplicate.update(row)

    assert first.count == second.count == 2
    assert first.hexdigest() == second.hexdigest()
    assert duplicate.count == 3
    assert duplicate.hexdigest() != first.hexdigest()


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


def test_legacy_baseline_adoption_against_real_store(
    postgres_dsn: str, tmp_path: Path
):
    from uuid import uuid4

    import psycopg  # type: ignore[import]

    from services.trade_journey.projection_store import ProjectionStore

    schema_name = f"test_legacy_baseline_{uuid4().hex[:8]}"
    store = ProjectionStore(postgres_dsn, schema=schema_name, bootstrap=True)
    source = tmp_path / "controller_state.json"
    source.write_text(
        json.dumps(_legacy_controller_state(), sort_keys=True), encoding="utf-8"
    )
    coordinator = LegacyBundleBackfillCoordinator(
        store,
        controller_id="canonical-lifecycle-projector",
        tenant_scope="*",
        environment_scope="*",
        controller_state_path=source,
        expected_sha256=sha256_file(source),
        snapshot_path=tmp_path / "legacy.snapshot.json",
        accepted_checkpoint=8,
        accepted_controller_deployment_sha="a" * 40,
        deployment_sha="b" * 40,
        batch_size=1,
    )
    try:
        result = coordinator.run()
        assert result["import_complete"] is True
        assert result["live_controller_seeded"] is True
        assert result["accepted_live"] is False

        migration = store.get_controller_state(
            "canonical-lifecycle-projector-migrate", "*", "*"
        )
        live = store.get_controller_state(
            "canonical-lifecycle-projector", "*", "*"
        )
        assert migration is not None
        assert migration.checkpoint_seq == 8
        assert migration.source_high_watermark == 8
        assert migration.backlog_count == 0
        assert migration.mode == "backfill"
        assert migration.status == "ready"
        assert migration.accepted_live is False
        assert live is not None
        assert live.checkpoint_seq == 8
        assert live.source_high_watermark == 8
        assert live.backlog_count == 0
        assert live.mode == "recovery"
        assert live.status == "repair_only"
        assert live.accepted_live is False

        from scripts.lifecycle_projector_parity import (
            _stream_legacy_baseline_parity,
        )

        parity = _stream_legacy_baseline_parity(
            dsn=postgres_dsn,
            schema=schema_name,
            controller_state=source,
            expected_sha256=sha256_file(source),
            controller_id="canonical-lifecycle-projector",
            legacy_checkpoint=8,
            classifications={},
        )
        assert parity["mismatch_count"] == 0
        assert parity["unexplained_mismatch_count"] == 0
        assert all(
            category["legacy_count"] == category["new_count"]
            for category in parity["categories"].values()
        )

        # A completed exact-checksum retry does not replay any baseline rows.
        rerun = coordinator.run()
        assert rerun["batches"] == result["batches"]
        with psycopg.connect(postgres_dsn) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {schema_name}.event_receipts")
            assert cur.fetchone()[0] == 8
            cur.execute(f"SELECT COUNT(*) FROM {schema_name}.journeys")
            assert cur.fetchone()[0] == 1
            cur.execute(f"SELECT COUNT(*) FROM {schema_name}.loop_runs")
            assert cur.fetchone()[0] == 1
    finally:
        with psycopg.connect(postgres_dsn) as conn, conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")


def test_backfill_duplicate_and_interruption_against_real_store(
    postgres_dsn: str, tmp_path: Path
):
    """A durable duplicate and a new same-aggregate event share one DB batch.

    The gate proves the third fetch is exactly rows 7--10, so row 9's durable
    duplicate and row 10's new event cannot accidentally pass in separate
    transactions. It then checks every relational mutation surface plus
    category-hash and sampled-row parity against the source window.
    """
    from uuid import uuid4

    import psycopg  # type: ignore[import]

    from services.trade_journey.projection_store import ProjectionStore

    schema_name = f"test_migrate_dup_{uuid4().hex[:8]}"
    store = ProjectionStore(postgres_dsn, schema=schema_name, bootstrap=True)
    try:
        base_rows = lifecycle_rows()
        # Source rows 1--8 are already durable before the final fetch. The ninth
        # fetched item repeats row 1 exactly (including its database-owned source
        # sequence); the tenth item is source growth for the same journey/loop.
        # Assigning a new sequence to the duplicate would fabricate an impossible
        # telemetry_events row because event_id and ingested_seq are both unique.
        dup_row = copy.deepcopy(base_rows[0])

        new_event = copy.deepcopy(base_rows[-1])
        new_event["event_id"] = "evt-paper-009"
        new_event["ingested_seq"] = 9
        new_event["created_at"] = "2026-07-15T00:00:09Z"
        new_event["ingested_at"] = "2026-07-15T00:01:10Z"
        new_event["payload"]["event_id"] = new_event["event_id"]
        new_event["payload"]["created_at"] = new_event["created_at"]

        all_rows = base_rows + [dup_row, new_event]
        snapshot_path = tmp_path / "snapshot.json"

        # Interruption 1: Process batch 1 (size 3) -> rows 1..3
        coord1 = BackfillCoordinator(
            store,
            controller_id="tj-proj-dup-it",
            tenant_scope="tenant-a",
            environment_scope="paper",
            fetch_batch=_paged_fetch(base_rows),
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
            fetch_batch=_paged_fetch(base_rows),
            snapshot_path=snapshot_path,
            batch_size=3,
        )
        res2 = coord2.run(max_batches=1)
        assert res2["batches"] == 1
        assert res2["checkpoint"] == 6

        controller_before = store.get_controller_state(
            "tj-proj-dup-it-migrate", "tenant-a", "paper"
        )
        assert controller_before is not None
        assert controller_before.checkpoint_seq == 6
        assert controller_before.projection_revision == 2

        duplicated_event_id = str(base_rows[0]["event_id"])
        with psycopg.connect(postgres_dsn) as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT event_id, ingested_seq, fingerprint, disposition,
                       projection_revision, projected_at
                FROM {schema_name}.event_receipts
                WHERE event_id = %s
                """,
                (duplicated_event_id,),
            )
            duplicate_receipt_before = cur.fetchone()
            cur.execute(
                f"""
                SELECT source_event_id, source_ingested_seq, stage_name,
                       stage_status, projection_revision, fingerprint, recorded_at
                FROM {schema_name}.journey_stages
                WHERE source_event_id = %s
                ORDER BY stage_name
                """,
                (duplicated_event_id,),
            )
            duplicate_stages_before = cur.fetchall()
        assert duplicate_receipt_before is not None
        assert duplicate_stages_before

        final_fetch_calls: list[tuple[int, int, list[int]]] = []
        final_window = all_rows[6:]

        def fetch_final_window(after_seq: int, limit: int) -> list[dict[str, Any]]:
            fetched = final_window if not final_fetch_calls else []
            # Record source-list ordinals, not ingested_seq: ordinal 9 is the
            # replay of source row 1 and ordinal 10 owns new source sequence 9.
            ordinals = [7, 8, 9, 10] if fetched else []
            final_fetch_calls.append((after_seq, limit, ordinals))
            return fetched

        # Interruption 3: one size-4 fetch and one projection transaction owns
        # rows 7--10, including duplicate row 9 and new row 10 together.
        coord3 = BackfillCoordinator(
            store,
            controller_id="tj-proj-dup-it",
            tenant_scope="tenant-a",
            environment_scope="paper",
            fetch_batch=fetch_final_window,
            snapshot_path=snapshot_path,
            batch_size=4,
        )
        res3 = coord3.run()
        assert final_fetch_calls == [(6, 4, [7, 8, 9, 10]), (9, 4, [])]
        assert res3 == {
            "batches": 1,
            "accepted": 3,
            "duplicates": 1,
            "quarantined": 0,
            "ignored": 0,
            "checkpoint": 9,
        }

        controller = store.get_controller_state(
            "tj-proj-dup-it-migrate", "tenant-a", "paper"
        )
        assert controller is not None
        assert controller.checkpoint_seq == 9
        assert controller.source_high_watermark == 9
        assert controller.backlog_count == 0
        assert controller.projection_revision == controller_before.projection_revision + 1
        assert controller.mode == "backfill"
        assert controller.accepted_live is False

        expected_rows = base_rows + [new_event]
        expected_event_ids = {str(row["event_id"]) for row in expected_rows}
        with psycopg.connect(postgres_dsn) as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT event_id, ingested_seq, source_event_type, disposition,
                       projection_revision
                FROM {schema_name}.event_receipts
                ORDER BY ingested_seq
                """
            )
            receipt_rows = cur.fetchall()
            cur.execute(
                f"""
                SELECT source_event_id, source_ingested_seq, stage_name,
                       stage_status, projection_revision, fingerprint
                FROM {schema_name}.journey_stages
                ORDER BY source_ingested_seq, source_event_id, stage_name
                """
            )
            stage_rows = cur.fetchall()
            cur.execute(
                f"""
                SELECT journey_id, status, last_ingested_seq, loop_run_id,
                       projection_revision
                FROM {schema_name}.journeys
                """
            )
            journey_rows = cur.fetchall()
            cur.execute(
                f"""
                SELECT loop_run_id, journey_id, lifecycle_summary,
                       freshness_lineage, projection_revision
                FROM {schema_name}.loop_runs
                """
            )
            loop_rows = cur.fetchall()
            cur.execute(f"SELECT COUNT(*) FROM {schema_name}.controller")
            controller_count = cur.fetchone()[0]
            cur.execute(
                f"""
                SELECT event_id, ingested_seq, fingerprint, disposition,
                       projection_revision, projected_at
                FROM {schema_name}.event_receipts
                WHERE event_id = %s
                """,
                (duplicated_event_id,),
            )
            duplicate_receipt_after = cur.fetchone()
            cur.execute(
                f"""
                SELECT source_event_id, source_ingested_seq, stage_name,
                       stage_status, projection_revision, fingerprint, recorded_at
                FROM {schema_name}.journey_stages
                WHERE source_event_id = %s
                ORDER BY stage_name
                """,
                (duplicated_event_id,),
            )
            duplicate_stages_after = cur.fetchall()

        # Receipt/stage sets contain each accepted canonical event once. The
        # duplicate fetched item never becomes a second receipt or stage, the new
        # event advances the controller, and the durable old rows remain unchanged.
        assert len(receipt_rows) == len(expected_rows) == 9
        assert {str(row[0]) for row in receipt_rows} == expected_event_ids
        assert [int(row[1]) for row in receipt_rows] == [*range(1, 10)]
        assert all(row[3] == "applied" for row in receipt_rows)
        assert len(stage_rows) == len(expected_rows) == 9
        assert {str(row[0]) for row in stage_rows} == expected_event_ids
        assert len({(row[0], row[2]) for row in stage_rows}) == len(stage_rows)
        assert duplicate_receipt_after == duplicate_receipt_before
        assert duplicate_stages_after == duplicate_stages_before

        assert journey_rows == [
            (
                "tj-paper-001",
                "completed",
                9,
                "lr-run-paper-001",
                controller.projection_revision,
            )
        ]
        assert len(loop_rows) == 1
        assert loop_rows[0][0] == "lr-run-paper-001"
        assert loop_rows[0][1] == "tj-paper-001"
        assert loop_rows[0][2]["canonical_event_count"] == len(expected_rows)
        assert loop_rows[0][3]["last_source_offset"] == 9
        assert loop_rows[0][4] == controller.projection_revision
        assert controller_count == 1

        # Stable category hashes and deterministic first/middle/last samples
        # provide a bounded drill-down in addition to full key/count equality.
        expected_category_counts = Counter(str(row["event_type"]) for row in expected_rows)
        actual_category_counts = Counter(str(row[2]) for row in receipt_rows)
        expected_categories = [
            {"source_event_type": event_type, "count": count}
            for event_type, count in sorted(expected_category_counts.items())
        ]
        actual_categories = [
            {"source_event_type": event_type, "count": count}
            for event_type, count in sorted(actual_category_counts.items())
        ]
        sample_source_rows = [
            expected_rows[0],
            expected_rows[len(expected_rows) // 2],
            expected_rows[-1],
        ]
        expected_samples = [
            {
                "event_id": str(row["event_id"]),
                "ingested_seq": int(row["ingested_seq"]),
                "source_event_type": str(row["event_type"]),
            }
            for row in sample_source_rows
        ]
        sample_ids = {row["event_id"] for row in expected_samples}
        actual_samples = [
            {
                "event_id": str(event_id),
                "ingested_seq": int(ingested_seq),
                "source_event_type": str(source_event_type),
            }
            for event_id, ingested_seq, source_event_type, _disposition, _revision in receipt_rows
            if str(event_id) in sample_ids
        ]
        parity = summarize_parity(
            [
                compare_category(
                    "receipt_category_counts",
                    expected_categories,
                    actual_categories,
                    key_fields=["source_event_type"],
                ),
                compare_category(
                    "receipt_samples",
                    expected_samples,
                    actual_samples,
                    key_fields=["ingested_seq", "event_id"],
                ),
            ]
        )
        assert parity["mismatch_count"] == 0, parity
        assert parity["unexplained_mismatch_count"] == 0, parity
        assert all(category["match"] for category in parity["categories"].values())

        persisted_snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        assert persisted_snapshot["checkpoint"] == 9

        # With the high watermark durably committed, a fresh coordinator sees
        # backlog zero and performs no additional transaction.
        coord4 = BackfillCoordinator(
            store,
            controller_id="tj-proj-dup-it",
            tenant_scope="tenant-a",
            environment_scope="paper",
            fetch_batch=_paged_fetch(expected_rows),
            snapshot_path=snapshot_path,
            batch_size=3,
        )
        res4 = coord4.run()
        assert res4["batches"] == 0
        assert res4["checkpoint"] == 9
    finally:
        with psycopg.connect(postgres_dsn) as conn, conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")

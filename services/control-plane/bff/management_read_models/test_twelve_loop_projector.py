"""Comprehensive test suite for TwelveLoopTruthProjector and TwelveLoopStore.

Verifies all acceptance criteria under LOOP-TRUTH-001 (pkt-pantheon-structural-closure-functional-v2-20260903,
SD §7.2, SA ADR-05):
  1. Persist release/correlation/loop observations from canonical stimulus, terminal and next-consumer receipts.
  2. Terminal plus next-consumer receipt is required for completion (absent next receipt = open).
  3. Incremental equals rebuild.
  4. Backfill cannot replace newer live truth.
  5. Static registry supplies labels and order only; registry maturity never sets runtime completion.
  6. Mandatory deletion: static or incident-derived success substitution excised.
  7. Rollback: disable the new read projection while preserving source receipts.
  8. Store durability, idempotency, and SQL schema validation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from unittest.mock import patch
import pytest


from services.control_plane.bff.management_read_models.twelve_loop_projector import (
    CANONICAL_TWELVE_LOOPS,
    CanonicalLoopReceipt,
    LoopObservation,
    TwelveLoopTruthProjector,
    resolve_loop_id_int,
)
from services.control_plane.bff.migrations.twelve_loop_truth import (
    MIGRATION_SQL_PATH,
    MemoryTwelveLoopStore,
    build_twelve_loop_store,
)


def _utc(seconds_offset: float = 0) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds_offset)


class TestTwelveLoopProjectorInvariants:
    """Test core invariants and acceptance criteria."""

    def test_canonical_twelve_loops_inventory(self) -> None:
        """SD §7.2, SA ADR-05: Exactly 12 canonical loops defined with owners."""
        assert len(CANONICAL_TWELVE_LOOPS) == 12
        for loop_id in range(1, 13):
            assert loop_id in CANONICAL_TWELVE_LOOPS
            canonical_id, name, owner = CANONICAL_TWELVE_LOOPS[loop_id]
            assert canonical_id
            assert name
            assert owner
            assert resolve_loop_id_int(loop_id) == loop_id
            assert resolve_loop_id_int(canonical_id) == loop_id

    def test_stimulus_receipt_marks_loop_open_pending_terminal(self) -> None:
        """Stimulus receipt marks observation open and awaiting terminal execution."""
        projector = TwelveLoopTruthProjector()
        now = _utc(-10)
        stimulus = CanonicalLoopReceipt(
            receipt_id="rcpt-stim-001",
            receipt_type="stimulus",
            loop_id=1,
            correlation_id="corr-test-01",
            release_id="rel-20260908",
            owner="source-ingest connector",
            provenance="live",
            observed_at=now,
            causation_id="cause-001",
        )
        obs = projector.ingest_receipt(stimulus)
        assert obs.status == "open"
        assert obs.stimulus_id == "rcpt-stim-001"
        assert obs.terminal_id is None
        assert obs.terminal_status == "pending"
        assert obs.freshness_status == "fresh"
        assert obs.provenance == "live"
        assert "awaiting terminal" in (obs.degradation_reason or "")

    def test_terminal_without_next_consumer_remains_open(self) -> None:
        """Acceptance: Terminal plus next-consumer receipt is required for completion.

        Absent next receipt means open, not complete.
        """
        projector = TwelveLoopTruthProjector()
        now = _utc(-10)
        stimulus = CanonicalLoopReceipt(
            receipt_id="rcpt-stim-002",
            receipt_type="stimulus",
            loop_id=2,
            correlation_id="corr-test-02",
            release_id="rel-20260908",
            owner="distillation connector",
            provenance="live",
            observed_at=now,
        )
        terminal = CanonicalLoopReceipt(
            receipt_id="rcpt-term-002",
            receipt_type="terminal",
            loop_id=2,
            correlation_id="corr-test-02",
            release_id="rel-20260908",
            owner="distillation worker",
            provenance="live",
            status="completed",
            observed_at=now + timedelta(seconds=2),
        )
        projector.ingest_receipt(stimulus)
        obs = projector.ingest_receipt(terminal)

        # MUST BE OPEN, not complete!
        assert obs.status == "open"
        assert obs.terminal_id == "rcpt-term-002"
        assert obs.terminal_status == "completed"
        assert obs.next_consumer_receipt_id is None
        assert "awaiting next-consumer" in (obs.degradation_reason or "")

    def test_terminal_plus_next_consumer_receipt_marks_complete(self) -> None:
        """Acceptance: Terminal plus next-consumer receipt is required for completion."""
        projector = TwelveLoopTruthProjector()
        now = _utc(-10)
        stimulus = CanonicalLoopReceipt(
            receipt_id="rcpt-stim-003",
            receipt_type="stimulus",
            loop_id=3,
            correlation_id="corr-test-03",
            release_id="rel-20260908",
            owner="alpha-replication-controller",
            provenance="live",
            observed_at=now,
        )
        terminal = CanonicalLoopReceipt(
            receipt_id="rcpt-term-003",
            receipt_type="terminal",
            loop_id=3,
            correlation_id="corr-test-03",
            release_id="rel-20260908",
            owner="alpha-replication-controller",
            provenance="live",
            status="success",
            observed_at=now + timedelta(seconds=1),
        )
        next_consumer = CanonicalLoopReceipt(
            receipt_id="rcpt-next-003",
            receipt_type="next_consumer",
            loop_id=3,
            correlation_id="corr-test-03",
            release_id="rel-20260908",
            owner="persona-teaching-controller",
            provenance="live",
            observed_at=now + timedelta(seconds=2),
        )

        projector.ingest_receipts([stimulus, terminal, next_consumer])
        obs = projector.get_observation("rel-20260908", "corr-test-03", 3)
        assert obs is not None
        assert obs.status == "complete"
        assert obs.terminal_id == "rcpt-term-003"
        assert obs.next_consumer_receipt_id == "rcpt-next-003"
        assert obs.degradation_reason is None
        assert obs.freshness_status == "fresh"

    def test_terminal_failure_marks_failed(self) -> None:
        """Terminal failure marks observation failed with reason."""
        projector = TwelveLoopTruthProjector()
        now = _utc(-5)
        terminal = CanonicalLoopReceipt(
            receipt_id="rcpt-term-fail-001",
            receipt_type="terminal",
            loop_id=5,
            correlation_id="corr-fail",
            release_id="rel-fail",
            owner="agora store",
            provenance="live",
            status="failed",
            degradation_reason="strategy simulation breach",
            observed_at=now,
        )
        obs = projector.ingest_receipt(terminal)
        assert obs.status == "failed"
        assert obs.terminal_status == "failed"
        assert "strategy simulation breach" in (obs.degradation_reason or "")

    def test_incremental_equals_rebuild(self) -> None:
        """Acceptance: Incremental update equals rebuild output."""
        projector = TwelveLoopTruthProjector()
        now = _utc(-30)

        receipts = [
            CanonicalLoopReceipt(
                receipt_id=f"rcpt-stim-{i}",
                receipt_type="stimulus",
                loop_id=i,
                correlation_id=f"corr-loop-{i}",
                release_id="rel-inc-test",
                owner=CANONICAL_TWELVE_LOOPS[i][2],
                provenance="live",
                observed_at=now + timedelta(seconds=i),
            )
            for i in range(1, 13)
        ] + [
            CanonicalLoopReceipt(
                receipt_id=f"rcpt-term-{i}",
                receipt_type="terminal",
                loop_id=i,
                correlation_id=f"corr-loop-{i}",
                release_id="rel-inc-test",
                owner=CANONICAL_TWELVE_LOOPS[i][2],
                provenance="live",
                status="completed",
                observed_at=now + timedelta(seconds=i + 1),
            )
            for i in range(1, 7)
        ] + [
            CanonicalLoopReceipt(
                receipt_id=f"rcpt-next-{i}",
                receipt_type="next_consumer",
                loop_id=i,
                correlation_id=f"corr-loop-{i}",
                release_id="rel-inc-test",
                owner="downstream-consumer",
                provenance="live",
                observed_at=now + timedelta(seconds=i + 2),
            )
            for i in range(1, 4)
        ]

        # Ingest incrementally
        for r in receipts:
            projector.ingest_receipt(r)

        incremental_observations = {
            (o.release_id, o.correlation_id, o.loop_id): o.to_dict()
            for o in projector.list_observations(release_id="rel-inc-test")
        }

        # Rebuild
        rebuilt = projector.rebuild()
        rebuild_observations = {
            (o.release_id, o.correlation_id, o.loop_id): o.to_dict()
            for o in rebuilt
            if o.release_id == "rel-inc-test"
        }

        assert len(incremental_observations) == 12
        assert incremental_observations == rebuild_observations

    def test_backfill_cannot_replace_newer_live_truth(self) -> None:
        """Acceptance: Backfill cannot replace newer live truth."""
        projector = TwelveLoopTruthProjector()
        now = _utc(-10)

        # 1. Live observation completed
        live_stim = CanonicalLoopReceipt(
            receipt_id="live-stim-01",
            receipt_type="stimulus",
            loop_id=8,
            correlation_id="corr-backfill-guard",
            release_id="rel-v1",
            owner="deployment orchestrator",
            provenance="live",
            observed_at=now,
        )
        live_term = CanonicalLoopReceipt(
            receipt_id="live-term-01",
            receipt_type="terminal",
            loop_id=8,
            correlation_id="corr-backfill-guard",
            release_id="rel-v1",
            owner="deployment orchestrator",
            provenance="live",
            status="completed",
            observed_at=now + timedelta(seconds=2),
        )
        live_next = CanonicalLoopReceipt(
            receipt_id="live-next-01",
            receipt_type="next_consumer",
            loop_id=8,
            correlation_id="corr-backfill-guard",
            release_id="rel-v1",
            owner="capital pool execution",
            provenance="live",
            observed_at=now + timedelta(seconds=3),
        )

        projector.ingest_receipts([live_stim, live_term, live_next])
        obs_live = projector.get_observation("rel-v1", "corr-backfill-guard", 8)
        assert obs_live is not None
        assert obs_live.status == "complete"
        assert obs_live.provenance == "live"

        # 2. Backfill arrives claiming failure or older open state
        backfill_term = CanonicalLoopReceipt(
            receipt_id="backfill-term-01",
            receipt_type="terminal",
            loop_id=8,
            correlation_id="corr-backfill-guard",
            release_id="rel-v1",
            owner="deployment orchestrator",
            provenance="backfill",
            status="failed",
            degradation_reason="historical run failed in older replay",
            observed_at=now - timedelta(hours=2),
        )

        projector.ingest_receipt(backfill_term)

        # Backfill must NOT overwrite the newer live observation!
        obs_after_backfill = projector.get_observation("rel-v1", "corr-backfill-guard", 8)
        assert obs_after_backfill is not None
        assert obs_after_backfill.status == "complete"
        assert obs_after_backfill.terminal_status == "completed"
        assert obs_after_backfill.terminal_id == "live-term-01"
        assert obs_after_backfill.provenance == "live"

    def test_static_registry_supplies_labels_only_never_runtime_completion(self) -> None:
        """Acceptance: Static registry supplies label/order only.

        Registry maturity never sets runtime completion; unobserved loops remain
        explicitly unobserved/unavailable.
        """
        projector = TwelveLoopTruthProjector()
        rows = projector.project_twelve_canonical_loops("rel-clean", "corr-clean")

        assert len(rows) == 12
        for idx, row in enumerate(rows, start=1):
            assert row["loop_id"] == idx
            assert row["canonical_id"] == CANONICAL_TWELVE_LOOPS[idx][0]
            assert row["loop_name"] == CANONICAL_TWELVE_LOOPS[idx][1]
            assert row["owner"] == CANONICAL_TWELVE_LOOPS[idx][2]
            # Must NOT claim success or completion!
            assert row["status"] == "unobserved"
            assert row["freshness_status"] == "unavailable"
            assert row["terminal_status"] == "unobserved"
            assert "no runtime receipts observed" in row["degradation_reason"]

    def test_rollback_disable_read_projection_preserves_source_receipts(self) -> None:
        """Rollback: Disable the new read projection while preserving source receipts."""
        projector = TwelveLoopTruthProjector()
        now = _utc(-5)

        receipt = CanonicalLoopReceipt(
            receipt_id="receipt-rollback-01",
            receipt_type="terminal",
            loop_id=9,
            correlation_id="corr-rb",
            release_id="rel-rb",
            owner="capital pool",
            provenance="live",
            status="completed",
            observed_at=now,
        )
        projector.ingest_receipt(receipt)

        # Normal mode: observation exists
        assert projector.get_observation("rel-rb", "corr-rb", 9) is not None

        # Disable projection (rollback)
        projector.disable_projection()
        assert not projector.enabled
        assert projector.get_observation("rel-rb", "corr-rb", 9) is None
        assert projector.list_observations(release_id="rel-rb") == []

        # Rows return explicit typed rollback degradation
        rb_rows = projector.project_twelve_canonical_loops("rel-rb", "corr-rb")
        assert len(rb_rows) == 12
        for r in rb_rows:
            assert r["status"] == "unobserved"
            assert r["freshness_status"] == "unavailable"
            assert "rollback mode" in r["degradation_reason"]

        # Re-enable projection: underlying receipts are intact!
        projector.enable_projection()
        restored_obs = projector.get_observation("rel-rb", "corr-rb", 9)
        assert restored_obs is not None
        assert restored_obs.terminal_id == "receipt-rollback-01"

    def test_receipt_ingestion_is_idempotent(self) -> None:
        """All writes are idempotent by receipt identity."""
        projector = TwelveLoopTruthProjector()
        now = _utc(-5)
        receipt = CanonicalLoopReceipt(
            receipt_id="idemp-rcpt-001",
            receipt_type="stimulus",
            loop_id=4,
            correlation_id="corr-idemp",
            release_id="rel-idemp",
            owner="teaching store",
            provenance="live",
            observed_at=now,
        )

        obs1 = projector.ingest_receipt(receipt)
        obs2 = projector.ingest_receipt(receipt)
        assert obs1 == obs2
        assert obs1.receipt_ids == ["idemp-rcpt-001"]

    def test_stale_observation_detection(self) -> None:
        """Freshness explicitly transitions to stale when exceeding max_age_seconds."""
        projector = TwelveLoopTruthProjector(max_age_seconds=60)
        old_time = _utc(-120)  # 120s ago > 60s
        stimulus = CanonicalLoopReceipt(
            receipt_id="rcpt-old-01",
            receipt_type="stimulus",
            loop_id=10,
            correlation_id="corr-stale",
            release_id="rel-stale",
            owner="telemetry reconciler",
            provenance="live",
            observed_at=old_time,
        )
        obs = projector.ingest_receipt(stimulus)
        assert obs.freshness_status == "stale"
        assert "exceeds freshness window" in (obs.degradation_reason or "")


class TestTwelveLoopStoreAndMigration:
    """Test store operations and SQL migration schema."""

    def test_memory_twelve_loop_store_operations(self) -> None:
        store = MemoryTwelveLoopStore()
        now = _utc(-10)

        receipt = CanonicalLoopReceipt(
            receipt_id="mem-rcpt-01",
            receipt_type="terminal",
            loop_id=11,
            correlation_id="corr-mem",
            release_id="rel-mem",
            owner="evolution engine",
            provenance="live",
            status="completed",
            observed_at=now,
        )
        store.record_receipt(receipt)
        receipts = store.list_receipts(release_id="rel-mem")
        assert len(receipts) == 1
        assert receipts[0].receipt_id == "mem-rcpt-01"

        obs = LoopObservation(
            release_id="rel-mem",
            correlation_id="corr-mem",
            loop_id=11,
            owner="evolution engine",
            status="complete",
            freshness_status="fresh",
            provenance="live",
            observed_at=now,
        )
        store.upsert_observation(obs)
        fetched = store.get_observation("rel-mem", "corr-mem", 11)
        assert fetched is not None
        assert fetched.status == "complete"

        store.clear_observations()
        assert store.get_observation("rel-mem", "corr-mem", 11) is None
        # Receipts remain preserved!
        assert len(store.list_receipts(release_id="rel-mem")) == 1

    def test_sql_migration_file_exists_and_declares_schema(self) -> None:
        """Verify 002_create_twelve_loop_truth_schema.sql schema and constraints."""
        assert MIGRATION_SQL_PATH.exists(), f"Missing {MIGRATION_SQL_PATH}"
        sql_content = MIGRATION_SQL_PATH.read_text(encoding="utf-8")

        # Schema and tables
        assert "CREATE SCHEMA IF NOT EXISTS loop_truth_projection;" in sql_content
        assert "loop_truth_projection.loop_receipts" in sql_content
        assert "loop_truth_projection.twelve_loop_observations" in sql_content

        # Key constraints and indexes
        assert "receipt_type IN ('stimulus', 'terminal', 'next_consumer')" in sql_content
        assert "provenance IN ('live', 'replay', 'backfill')" in sql_content
        assert "CHECK (loop_id BETWEEN 1 AND 12)" in sql_content
        assert "PRIMARY KEY (release_id, correlation_id, loop_id)" in sql_content
        assert "idx_loop_receipts_key" in sql_content
        assert "idx_loop_obs_release_corr" in sql_content


class TestTwelveLoopProjectorReviewRegressions:
    """Independent review regression coverage for deterministic reduction, durability, and freshness."""

    @staticmethod
    def _receipt(rid: str, kind: str, status: str = "", provenance: str = "live", offset: float = 0.0) -> CanonicalLoopReceipt:
        now = datetime.now(timezone.utc)
        return CanonicalLoopReceipt(
            receipt_id=rid,
            receipt_type=kind,  # type: ignore[arg-type]
            loop_id=1,
            correlation_id="review-corr",
            release_id="review-release",
            owner="source-owner",
            provenance=provenance,  # type: ignore[arg-type]
            status=status,
            observed_at=now + timedelta(seconds=offset),
        )

    def test_out_of_order_incremental_equals_rebuild(self) -> None:
        """P1: Live terminal out of order preserves newest live truth and incremental equals rebuild."""
        p = TwelveLoopTruthProjector()
        p.ingest_receipts([
            self._receipt("new-failure", "terminal", "failed"),
            self._receipt("old-success", "terminal", "completed", offset=-10),
            self._receipt("next", "next_consumer", "completed", offset=1),
        ])
        before = p.get_observation("review-release", "review-corr", 1).to_dict()
        p.rebuild()
        after = p.get_observation("review-release", "review-corr", 1).to_dict()
        assert before["status"] == after["status"] == "failed"

    def test_rebuild_does_not_promote_backfill_terminal_to_live(self) -> None:
        """P1: Rebuild does not promote an older backfill terminal to satisfy a live stimulus."""
        p = TwelveLoopTruthProjector()
        p.ingest_receipts([
            self._receipt("live-start", "stimulus"),
            self._receipt("historical-success", "terminal", "completed", "backfill", -10),
            self._receipt("next", "next_consumer", "completed", offset=1),
        ])
        before = p.get_observation("review-release", "review-corr", 1).to_dict()
        p.rebuild()
        after = p.get_observation("review-release", "review-corr", 1).to_dict()
        assert before["status"] == after["status"] == "open"

    def test_failed_next_consumer_is_not_completion(self) -> None:
        """P1: Downstream consumer rejection fails the loop and does not mark complete."""
        p = TwelveLoopTruthProjector()
        p.ingest_receipts([
            self._receipt("stimulus", "stimulus"),
            self._receipt("terminal", "terminal", "completed"),
            self._receipt("rejected-next", "next_consumer", "rejected"),
        ])
        obs = p.get_observation("review-release", "review-corr", 1).to_dict()
        assert obs["status"] != "complete"
        assert obs["status"] == "failed"

    def test_read_freshness_expires_without_new_receipts(self) -> None:
        """P2: Read APIs dynamically recompute freshness against current time."""
        now = datetime.now(timezone.utc)
        p = TwelveLoopTruthProjector(max_age_seconds=60)
        p.ingest_receipt(self._receipt("stimulus", "stimulus"))

        class Later(datetime):
            @classmethod
            def now(cls, tz=None):
                return now + timedelta(seconds=120)

        with patch("services.control_plane.bff.management_read_models.twelve_loop_projector.datetime", Later):
            row = p.project_twelve_canonical_loops("review-release", "review-corr")[0]
        assert row["freshness_status"] == "stale"

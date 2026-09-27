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
from unittest.mock import patch
from uuid import uuid4
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
            status="accepted",
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
            status="accepted",
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
    def _receipt(
        rid: str,
        kind: str,
        status: str = "",
        provenance: str = "live",
        offset: float = 0.0,
        cause: Optional[str] = None,
    ) -> CanonicalLoopReceipt:
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
            causation_id=cause,
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

    def test_pending_consumer_does_not_complete(self) -> None:
        """P1: Next-consumer status pending or unknown keeps observation open."""
        p = TwelveLoopTruthProjector()
        p.ingest_receipts([
            self._receipt("s", "stimulus"),
            self._receipt("t", "terminal", "completed", cause="s"),
            self._receipt("n", "next_consumer", "pending", cause="t"),
        ])
        obs = p.get_observation("review-release", "review-corr", 1)
        assert obs is not None
        assert obs.status == "open"
        assert "awaiting acknowledgement or completion" in (obs.degradation_reason or "")

    def test_unrelated_causation_does_not_complete(self) -> None:
        """P1: Causation continuity binds receipts to the selected causal chain."""
        p = TwelveLoopTruthProjector()
        p.ingest_receipts([
            self._receipt("s", "stimulus"),
            self._receipt("t", "terminal", "completed", cause="different-stimulus"),
            self._receipt("n", "next_consumer", "accepted", cause="different-terminal"),
        ])
        obs = p.get_observation("review-release", "review-corr", 1)
        assert obs is not None
        assert obs.status != "complete"
        assert obs.status == "open"

    def test_retry_chain_causation_continuity(self) -> None:
        """P1: Retry chain correctly binds retry terminal and consumer; aborted consumer cannot satisfy retry."""
        p = TwelveLoopTruthProjector()
        stimulus = self._receipt("s", "stimulus", offset=0)
        t1_failed = self._receipt("t1", "terminal", "failed", offset=1, cause="s")
        n1_aborted = self._receipt("n1", "next_consumer", "rejected", offset=2, cause="t1")
        t2_retry = self._receipt("t2", "terminal", "completed", offset=3, cause="s")

        # Ingest up to retry terminal without retry consumer: must remain open
        p.ingest_receipts([stimulus, t1_failed, n1_aborted, t2_retry])
        obs_mid = p.get_observation("review-release", "review-corr", 1)
        assert obs_mid is not None
        assert obs_mid.terminal_id == "t2"
        assert obs_mid.status == "open"
        assert obs_mid.next_consumer_receipt_id is None

        # Ingest matching consumer for retry terminal: completes
        n2_retry = self._receipt("n2", "next_consumer", "accepted", offset=4, cause="t2")
        p.ingest_receipt(n2_retry)
        obs_done = p.get_observation("review-release", "review-corr", 1)
        assert obs_done is not None
        assert obs_done.terminal_id == "t2"
        assert obs_done.next_consumer_receipt_id == "n2"
        assert obs_done.status == "complete"

    def test_failed_persistence_can_be_retried_before_reporting_durable_truth(self) -> None:
        """P1: Store outage raises explicit retryable exception; retry succeeds and fresh projector reloads."""
        class InitiallyOffline(MemoryTwelveLoopStore):
            offline = True
            def record_receipt(self, r: CanonicalLoopReceipt) -> None:
                if self.offline:
                    raise ConnectionError("review simulated database outage")
                return super().record_receipt(r)
            def upsert_observation(self, o: LoopObservation) -> None:
                if self.offline:
                    raise ConnectionError("review simulated database outage")
                return super().upsert_observation(o)

        store = InitiallyOffline()
        p = TwelveLoopTruthProjector(store)
        receipts = [
            self._receipt("s", "stimulus"),
            self._receipt("t", "terminal", "completed"),
            self._receipt("n", "next_consumer", "accepted"),
        ]
        for r in receipts:
            with pytest.raises(ConnectionError):
                p.ingest_receipt(r)

        # Store was offline; nothing was recorded
        assert len(store.list_receipts()) == 0

        # Store recovers; retry ingestion
        store.offline = False
        p.ingest_receipts(receipts)
        assert len(store.list_receipts()) == 3

        # Fresh instance reloads from store and verifies complete status
        fresh = TwelveLoopTruthProjector(store)
        obs = fresh.get_observation("review-release", "review-corr", 1)
        assert obs is not None
        assert obs.status == "complete"

    def test_ignored_backfill_does_not_refresh_live_observation(self) -> None:
        """P2: Freshness is derived only from accepted evidence; ignored backfill cannot refresh live observation."""
        p = TwelveLoopTruthProjector(max_age_seconds=60)
        p.ingest_receipts([
            self._receipt("s", "stimulus", offset=-120),
            self._receipt("t", "terminal", "completed", offset=-120),
            self._receipt("n", "next_consumer", "accepted", offset=-120),
        ])
        obs_stale = p.get_observation("review-release", "review-corr", 1)
        assert obs_stale is not None
        assert obs_stale.freshness_status == "stale"

        # Ignored backfill arrives with newer timestamp
        p.ingest_receipt(self._receipt("historical", "terminal", "failed", provenance="backfill", offset=0))
        obs_after = p.get_observation("review-release", "review-corr", 1)
        assert obs_after is not None
        assert obs_after.terminal_id == "t"
        assert obs_after.freshness_status == "stale"

    def test_stale_degradation_reason_does_not_duplicate(self) -> None:
        """P2: Repeated freshness evaluations do not duplicate 'exceeds freshness window' strings."""
        p = TwelveLoopTruthProjector(max_age_seconds=60)
        p.ingest_receipt(self._receipt("s", "stimulus", offset=-120))
        obs1 = p.get_observation("review-release", "review-corr", 1)
        assert obs1 is not None
        obs2 = p.get_observation("review-release", "review-corr", 1)
        assert obs2 is not None
        reason = obs2.degradation_reason or ""
        assert reason.count("exceeds freshness window") == 1

    def test_cross_key_duplicate_receipt_id_rejected(self) -> None:
        """P1: Reject known receipt ID under a conflicting correlation or key."""
        store = MemoryTwelveLoopStore()
        p = TwelveLoopTruthProjector(store=store)

        term_c1 = CanonicalLoopReceipt(
            receipt_id="term-shared-01",
            receipt_type="terminal",
            loop_id=1,
            correlation_id="c1",
            release_id="rel-1",
            owner="source-ingest connector",
            provenance="live",
            status="failed",
            observed_at=_utc(-10),
        )
        term_c2 = CanonicalLoopReceipt(
            receipt_id="term-shared-01",
            receipt_type="terminal",
            loop_id=1,
            correlation_id="c2",
            release_id="rel-1",
            owner="source-ingest connector",
            provenance="live",
            status="completed",
            observed_at=_utc(-5),
        )

        p.ingest_receipt(term_c1)
        assert p.get_observation("rel-1", "c1", 1).status == "failed"

        # Conflicting identity under c2 must be rejected
        with pytest.raises(ValueError, match="Conflicting receipt identity"):
            p.ingest_receipt(term_c2)

    def test_same_key_unpersisted_conflicting_content_binds_to_persisted_receipt(self) -> None:
        """P1: When re-ingesting known receipt ID under same key, bind to persisted receipt, never incoming unpersisted content."""
        store = MemoryTwelveLoopStore()
        persisted = CanonicalLoopReceipt(
            receipt_id="rcpt-bind-01",
            receipt_type="terminal",
            loop_id=1,
            correlation_id="c-bind",
            release_id="rel-bind",
            owner="source-ingest connector",
            provenance="live",
            status="failed",
            observed_at=_utc(-10),
        )
        store.record_receipt(persisted)

        # Incoming content claims status="completed"
        incoming = CanonicalLoopReceipt(
            receipt_id="rcpt-bind-01",
            receipt_type="terminal",
            loop_id=1,
            correlation_id="c-bind",
            release_id="rel-bind",
            owner="source-ingest connector",
            provenance="live",
            status="completed",
            observed_at=_utc(-5),
        )

        p = TwelveLoopTruthProjector(store=store)
        obs = p.ingest_receipt(incoming)
        # Must reduce the persisted failed terminal, not the incoming completed terminal
        assert obs.status == "failed"
        assert obs.terminal_status == "failed"

    def test_partial_write_retry_succeeds_in_memory_store(self) -> None:
        """P1: Retry after receipt recorded but observation not written completes successfully."""
        store = MemoryTwelveLoopStore()
        stimulus = CanonicalLoopReceipt(
            receipt_id="rcpt-retry-01",
            receipt_type="stimulus",
            loop_id=1,
            correlation_id="c-retry",
            release_id="rel-retry",
            owner="source-ingest connector",
            provenance="live",
            observed_at=_utc(-5),
        )
        # Partially written: receipt recorded in store
        store.record_receipt(stimulus)
        assert store.get_observation("rel-retry", "c-retry", 1) is None

        p = TwelveLoopTruthProjector(store=store)
        obs = p.ingest_receipt(stimulus)
        assert obs.status == "open"
        assert obs.stimulus_id == "rcpt-retry-01"
        assert store.get_observation("rel-retry", "c-retry", 1) is not None

    def test_two_projectors_shared_store_stale_backfill_reduction(self) -> None:
        """P1: Two projectors on one store: Projector 1 ingests live truth,

        Projector 2 ingests older backfill terminal.
        Reduction serializes against canonical stored receipts and fences stale writers.
        """
        store = MemoryTwelveLoopStore()
        now = _utc(-10)
        stimulus = CanonicalLoopReceipt(
            receipt_id="rcpt-s-1",
            receipt_type="stimulus",
            loop_id=1,
            correlation_id="c-shared",
            release_id="rel-shared",
            owner="source-ingest connector",
            provenance="live",
            observed_at=now,
        )
        terminal = CanonicalLoopReceipt(
            receipt_id="rcpt-t-1",
            receipt_type="terminal",
            loop_id=1,
            correlation_id="c-shared",
            release_id="rel-shared",
            owner="source-ingest connector",
            provenance="live",
            status="completed",
            observed_at=now,
        )
        next_consumer = CanonicalLoopReceipt(
            receipt_id="rcpt-n-1",
            receipt_type="next_consumer",
            loop_id=1,
            correlation_id="c-shared",
            release_id="rel-shared",
            owner="distillation connector",
            provenance="live",
            status="accepted",
            observed_at=now,
        )

        p1 = TwelveLoopTruthProjector(store=store)
        p1.ingest_receipts([stimulus, terminal, next_consumer])
        assert p1.get_observation("rel-shared", "c-shared", 1).status == "complete"

        # Projector 2 is an independent instance without p1's local cache
        p2 = TwelveLoopTruthProjector(store=store, auto_load=False)
        backfill_fail = CanonicalLoopReceipt(
            receipt_id="rcpt-backfill-old",
            receipt_type="terminal",
            loop_id=1,
            correlation_id="c-shared",
            release_id="rel-shared",
            owner="source-ingest connector",
            provenance="backfill",
            status="failed",
            observed_at=now - timedelta(seconds=120),
        )
        obs2 = p2.ingest_receipt(backfill_fail)
        assert obs2.status == "complete"
        assert obs2.provenance == "live"

        # Durable store observation remains complete/live
        obs_store = store.get_observation("rel-shared", "c-shared", 1)
        assert obs_store is not None
        assert obs_store.status == "complete"
        assert obs_store.provenance == "live"

        # Fresh rebuild produces complete/live
        p3 = TwelveLoopTruthProjector(store=store)
        p3.rebuild()
        obs3 = p3.get_observation("rel-shared", "c-shared", 1)
        assert obs3 is not None
        assert obs3.status == "complete"
        assert obs3.provenance == "live"

    def test_equal_timestamp_interleaving_cannot_restore_complete(self) -> None:
        """P1 regression: interleaved terminal persistence at equal timestamp cannot be overwritten by stale complete."""
        prefix = uuid4().hex
        now = datetime.now(timezone.utc)

        def make(name: str, kind: str, status: str = "", offset: int = 0, cause: Optional[str] = None, corr: str = "c") -> CanonicalLoopReceipt:
            return CanonicalLoopReceipt(
                receipt_id=prefix + name,
                receipt_type=kind,
                release_id=prefix,
                correlation_id=corr,
                loop_id=1,
                owner="review",
                provenance="live",
                status=status,
                observed_at=now + timedelta(seconds=offset),
                causation_id=prefix + cause if cause else None,
            )

        store = MemoryTwelveLoopStore()
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

    def test_late_stimulus_incremental_equals_rebuild(self) -> None:
        """P1 regression: late stimulus invalidates old chain and incremental reduction equals rebuild."""
        prefix = uuid4().hex
        now = datetime.now(timezone.utc)

        def make(name: str, kind: str, status: str = "", offset: int = 0, cause: Optional[str] = None, corr: str = "c") -> CanonicalLoopReceipt:
            return CanonicalLoopReceipt(
                receipt_id=prefix + name,
                receipt_type=kind,
                release_id=prefix,
                correlation_id=corr,
                loop_id=1,
                owner="review",
                provenance="live",
                status=status,
                observed_at=now + timedelta(seconds=offset),
                causation_id=prefix + cause if cause else None,
            )

        store = MemoryTwelveLoopStore()
        p = TwelveLoopTruthProjector(store, auto_load=False)
        p.ingest_receipts([
            make("s", "stimulus", offset=-30),
            make("t", "terminal", "completed", offset=-20, cause="s"),
            make("n", "next_consumer", "accepted", offset=-10, cause="t"),
        ])
        incremental = p.ingest_receipt(make("s2", "stimulus", offset=-15)).status
        rebuilt = p.rebuild()[0].status
        assert incremental == rebuilt == "open"

    def test_duplicate_race_cannot_project_unpersisted_cross_key_receipt(self) -> None:
        """P1 regression: concurrent duplicate insert after get_receipt cannot project unpersisted content under another correlation."""
        prefix = uuid4().hex
        now = datetime.now(timezone.utc)

        def make(name: str, kind: str, status: str = "", offset: int = 0, cause: Optional[str] = None, corr: str = "c") -> CanonicalLoopReceipt:
            return CanonicalLoopReceipt(
                receipt_id=prefix + name,
                receipt_type=kind,
                release_id=prefix,
                correlation_id=corr,
                loop_id=1,
                owner="review",
                provenance="live",
                status=status,
                observed_at=now + timedelta(seconds=offset),
                causation_id=prefix + cause if cause else None,
            )

        store = MemoryTwelveLoopStore()
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
    def test_late_lower_provenance_stimulus_keeps_incremental_rebuild_equal_memory(self, provenance: str) -> None:
        """P1 regression: late lower-provenance stimulus cannot invalidate live terminal chain in memory store."""
        store = MemoryTwelveLoopStore()
        prefix = "mem-late-" + uuid4().hex
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

    def test_memory_store_receipt_set_containment_and_provenance_fencing(self) -> None:
        """P1 regression: MemoryTwelveLoopStore enforces receipt_ids containment before provenance fencing."""
        store = MemoryTwelveLoopStore()
        now = datetime.now(timezone.utc)
        obs_base = LoopObservation(
            release_id="r1",
            correlation_id="c1",
            loop_id=1,
            owner="review",
            status="complete",
            freshness_status="fresh",
            provenance="live",
            observed_at=now,
            receipt_ids=["rcpt-1", "rcpt-2"],
        )
        store.upsert_observation(obs_base)
        assert store.get_observation("r1", "c1", 1).status == "complete"

        # 1. Non-superset update with higher/equal provenance rejected
        obs_stale = LoopObservation(
            release_id="r1",
            correlation_id="c1",
            loop_id=1,
            owner="review",
            status="failed",
            freshness_status="fresh",
            provenance="live",
            observed_at=now + timedelta(seconds=10),
            receipt_ids=["rcpt-3"],
        )
        store.upsert_observation(obs_stale)
        assert store.get_observation("r1", "c1", 1).status == "complete"

        # 2. Superset update with lower provenance rejected
        obs_backfill = LoopObservation(
            release_id="r1",
            correlation_id="c1",
            loop_id=1,
            owner="review",
            status="failed",
            freshness_status="fresh",
            provenance="backfill",
            observed_at=now + timedelta(seconds=10),
            receipt_ids=["rcpt-1", "rcpt-2", "rcpt-3"],
        )
        store.upsert_observation(obs_backfill)
        assert store.get_observation("r1", "c1", 1).provenance == "live"
        assert store.get_observation("r1", "c1", 1).status == "complete"

    @pytest.mark.parametrize("provenance", ["replay", "backfill"])
    @pytest.mark.parametrize("arrival_order", ["live_first", "terminal_first"])
    def test_unmatched_live_next_consumer_keeps_incremental_rebuild_equal_memory(
        self, provenance: str, arrival_order: str
    ) -> None:
        """P1 regression: unmatched higher-provenance live next-consumer chain is preserved as deterministic candidate
        even when unrelated replay/backfill terminal candidate exists, across both arrival orders on Memory store."""
        store = MemoryTwelveLoopStore()
        prefix = "mem-orphan-next-" + uuid4().hex
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

    def test_equal_time_stimuli_reduce_independently_of_arrival_order_memory(self) -> None:
        """P1 regression: equal-time live/replay stimuli reduce identically across all arrival order permutations on Memory store."""
        from itertools import permutations

        key = "mem-eq-perm-" + uuid4().hex
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

        store = MemoryTwelveLoopStore()
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
    def test_older_stimulus_cannot_steal_live_next_consumer_memory(self, provenance: str) -> None:
        """P1 regression: older replay or backfill stimulus cannot steal live next-consumer or revert observed_at on Memory store."""
        key = "mem-late-stim-" + uuid4().hex
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

        store = MemoryTwelveLoopStore()
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

    def test_rebuild_persistence_failure_does_not_cache_unpersisted_complete(self) -> None:
        """P1: A rebuild-time observation write failure must not be cached as durable truth.

        If rebuild() swallows the store write exception and still caches the
        reduced observation in-memory, a subsequent ingest_receipt() of the
        same receipt short-circuits on the in-memory idempotency check and
        returns "complete" even though store.get_observation() is None (the
        write never actually landed).
        """
        key = "rebuild-fail-" + uuid4().hex
        now = datetime.now(timezone.utc)

        def receipt(name: str, kind: str, status: str, seconds: int) -> CanonicalLoopReceipt:
            return CanonicalLoopReceipt(
                receipt_id=key + name,
                receipt_type=kind,
                loop_id=1,
                correlation_id=key,
                release_id=key,
                owner="review",
                provenance="live",
                status=status,
                observed_at=now + timedelta(seconds=seconds),
                causation_id=key + "cause" if kind != "stimulus" else None,
            )

        store = MemoryTwelveLoopStore()
        stimulus = receipt("s", "stimulus", "", -20)
        terminal = receipt("t", "terminal", "completed", -10)
        next_consumer = receipt("n", "next_consumer", "accepted", 0)
        store.record_receipt(stimulus)
        store.record_receipt(terminal)
        store.record_receipt(next_consumer)

        offline = {"value": True}
        original_upsert = store.upsert_observation

        def flaky_upsert(obs: LoopObservation) -> None:
            if offline["value"]:
                raise ConnectionError("review simulated rebuild write failure")
            return original_upsert(obs)

        store.upsert_observation = flaky_upsert  # type: ignore[assignment]

        projector = TwelveLoopTruthProjector(store)  # auto_load triggers rebuild() while offline
        assert store.get_observation(key, key, 1) is None
        assert projector.get_observation(key, key, 1) is None

        # Store recovers: re-ingesting the already-recorded terminal must retry
        # persistence rather than short-circuit on a cached non-durable "complete".
        offline["value"] = False
        obs = projector.ingest_receipt(terminal)
        assert obs.status == "complete"
        durable = store.get_observation(key, key, 1)
        assert durable is not None
        assert durable.status == "complete"

    def test_release_wide_correlation_tie_break_is_order_independent(self) -> None:
        """P2: Cross-correlation release-wide selection must not depend on insertion/iteration order.

        Two correlations under the same release/loop observed at the exact
        same timestamp must resolve to the same winner regardless of the
        order receipts were ingested in, and regardless of whether the
        projection is served from live in-memory state or a fresh reload
        from the store (dict iteration order differs across the two paths).
        """
        release = "release-tie-" + uuid4().hex
        now = datetime.now(timezone.utc)

        def receipt(corr: str, status: str) -> CanonicalLoopReceipt:
            return CanonicalLoopReceipt(
                receipt_id=release + corr,
                receipt_type="terminal",
                loop_id=1,
                correlation_id=corr,
                release_id=release,
                owner="review",
                provenance="live",
                status=status,
                observed_at=now,
            )

        z_failed = receipt("z-correlation", "failed")
        a_open = receipt("a-correlation", "")

        store_forward = MemoryTwelveLoopStore()
        forward = TwelveLoopTruthProjector(store_forward, auto_load=False)
        forward.ingest_receipts([z_failed, a_open])
        forward_rows = {
            row["loop_id"]: row for row in forward.project_twelve_canonical_loops(release)
        }

        store_reverse = MemoryTwelveLoopStore()
        reverse = TwelveLoopTruthProjector(store_reverse, auto_load=False)
        reverse.ingest_receipts([a_open, z_failed])
        reverse_rows = {
            row["loop_id"]: row for row in reverse.project_twelve_canonical_loops(release)
        }

        assert forward_rows[1]["correlation_id"] == reverse_rows[1]["correlation_id"]
        assert forward_rows[1]["status"] == reverse_rows[1]["status"]

        # A fresh reload from the (forward-ingested) store must pick the same
        # winner as the live projector that produced it.
        reloaded = TwelveLoopTruthProjector(store_forward)
        reloaded_rows = {
            row["loop_id"]: row for row in reloaded.project_twelve_canonical_loops(release)
        }
        assert reloaded_rows[1]["correlation_id"] == forward_rows[1]["correlation_id"]
        assert reloaded_rows[1]["status"] == forward_rows[1]["status"]


class TestTwelveLoopProjectorScopeAndStorage:
    """Test explicit tenant and environment scope under LOOP-RECEIPT-SCOPE-STORAGE-001."""

    def test_canonical_receipt_and_observation_explicit_scope(self) -> None:
        """Scope is explicit on CanonicalLoopReceipt and LoopObservation, and never inferred from payload."""
        now = _utc()
        # Scope is supplied explicitly via trusted fields
        receipt = CanonicalLoopReceipt(
            receipt_id="rcpt-scoped-01",
            receipt_type="terminal",
            loop_id=1,
            correlation_id="corr-01",
            release_id="rel-01",
            owner="source-ingest connector",
            provenance="live",
            tenant_id="tenant-alpha",
            environment="production",
            status="completed",
            observed_at=now,
            payload={"tenant_id": "malicious-inferred-tenant", "environment": "inferred-env"},
        )
        assert receipt.tenant_id == "tenant-alpha"
        assert receipt.environment == "production"

        # to_dict preserves scope
        d = receipt.to_dict()
        assert d["tenant_id"] == "tenant-alpha"
        assert d["environment"] == "production"

        # from_dict deserializes trusted scope fields, and ignores payload labels
        receipt_deserialized = CanonicalLoopReceipt.from_dict({
            "receipt_id": "rcpt-scoped-02",
            "receipt_type": "stimulus",
            "loop_id": 1,
            "correlation_id": "corr-02",
            "release_id": "rel-02",
            "tenant_id": "tenant-beta",
            "environment": "staging",
            "payload": {"tenant_id": "ignored-payload-tenant"},
        })
        assert receipt_deserialized.tenant_id == "tenant-beta"
        assert receipt_deserialized.environment == "staging"

        # LoopObservation carries scope
        obs = LoopObservation(
            release_id="rel-01",
            correlation_id="corr-01",
            loop_id=1,
            owner="source-ingest connector",
            tenant_id="tenant-alpha",
            environment="production",
            status="complete",
        )
        assert obs.tenant_id == "tenant-alpha"
        assert obs.environment == "production"
        obs_dict = obs.to_dict()
        assert obs_dict["tenant_id"] == "tenant-alpha"
        assert obs_dict["environment"] == "production"

    def test_cross_tenant_isolation_identical_keys(self) -> None:
        """Tenants alpha and beta share release/correlation/loop keys without cross-tenant collision or visibility."""
        store = MemoryTwelveLoopStore()
        projector = TwelveLoopTruthProjector(store, auto_load=False)
        now = _utc()

        # Tenant Alpha: complete loop
        alpha_stimulus = CanonicalLoopReceipt(
            receipt_id="alpha-stim",
            receipt_type="stimulus",
            loop_id=2,
            correlation_id="shared-corr",
            release_id="shared-rel",
            owner="distillation",
            provenance="live",
            tenant_id="tenant-alpha",
            environment="production",
            observed_at=now,
        )
        alpha_terminal = CanonicalLoopReceipt(
            receipt_id="alpha-term",
            receipt_type="terminal",
            loop_id=2,
            correlation_id="shared-corr",
            release_id="shared-rel",
            owner="distillation",
            provenance="live",
            tenant_id="tenant-alpha",
            environment="production",
            status="completed",
            observed_at=now + timedelta(seconds=1),
            causation_id="alpha-stim",
        )
        alpha_next = CanonicalLoopReceipt(
            receipt_id="alpha-next",
            receipt_type="next_consumer",
            loop_id=2,
            correlation_id="shared-corr",
            release_id="shared-rel",
            owner="alpha-rep",
            provenance="live",
            tenant_id="tenant-alpha",
            environment="production",
            status="accepted",
            observed_at=now + timedelta(seconds=2),
            causation_id="alpha-term",
        )

        # Tenant Beta: failed loop on SAME release and correlation
        beta_stimulus = CanonicalLoopReceipt(
            receipt_id="beta-stim",
            receipt_type="stimulus",
            loop_id=2,
            correlation_id="shared-corr",
            release_id="shared-rel",
            owner="distillation",
            provenance="live",
            tenant_id="tenant-beta",
            environment="production",
            observed_at=now,
        )
        beta_terminal = CanonicalLoopReceipt(
            receipt_id="beta-term",
            receipt_type="terminal",
            loop_id=2,
            correlation_id="shared-corr",
            release_id="shared-rel",
            owner="distillation",
            provenance="live",
            tenant_id="tenant-beta",
            environment="production",
            status="failed",
            observed_at=now + timedelta(seconds=1),
            causation_id="beta-stim",
        )

        # Ingest Tenant Alpha receipts
        projector.ingest_receipts([alpha_stimulus, alpha_terminal, alpha_next])
        # Ingest Tenant Beta receipts
        projector.ingest_receipts([beta_stimulus, beta_terminal])

        # Verify Alpha observation is complete and isolated
        obs_alpha = projector.get_observation("shared-rel", "shared-corr", 2, tenant_id="tenant-alpha", environment="production")
        assert obs_alpha is not None
        assert obs_alpha.status == "complete"
        assert obs_alpha.tenant_id == "tenant-alpha"
        assert obs_alpha.terminal_id == "alpha-term"
        assert obs_alpha.next_consumer_receipt_id == "alpha-next"

        # Verify Beta observation is failed and isolated
        obs_beta = projector.get_observation("shared-rel", "shared-corr", 2, tenant_id="tenant-beta", environment="production")
        assert obs_beta is not None
        assert obs_beta.status == "failed"
        assert obs_beta.tenant_id == "tenant-beta"
        assert obs_beta.terminal_id == "beta-term"

        # Store queries respect scope
        alpha_receipts = store.list_receipts(tenant_id="tenant-alpha", environment="production")
        assert len(alpha_receipts) == 3
        assert all(r.tenant_id == "tenant-alpha" for r in alpha_receipts)

        beta_receipts = store.list_receipts(tenant_id="tenant-beta", environment="production")
        assert len(beta_receipts) == 2
        assert all(r.tenant_id == "tenant-beta" for r in beta_receipts)

    def test_cross_environment_isolation_identical_keys(self) -> None:
        """Same tenant across production and staging environments are isolated."""
        store = MemoryTwelveLoopStore()
        projector = TwelveLoopTruthProjector(store, auto_load=False)
        now = _utc()

        prod_stim = CanonicalLoopReceipt(
            receipt_id="prod-stim",
            receipt_type="stimulus",
            loop_id=5,
            correlation_id="c-env",
            release_id="r-env",
            owner="agora",
            provenance="live",
            tenant_id="tenant-acme",
            environment="production",
            observed_at=now,
        )
        staging_stim = CanonicalLoopReceipt(
            receipt_id="staging-stim",
            receipt_type="stimulus",
            loop_id=5,
            correlation_id="c-env",
            release_id="r-env",
            owner="agora",
            provenance="live",
            tenant_id="tenant-acme",
            environment="staging",
            observed_at=now,
        )
        staging_term = CanonicalLoopReceipt(
            receipt_id="staging-term",
            receipt_type="terminal",
            loop_id=5,
            correlation_id="c-env",
            release_id="r-env",
            owner="agora",
            provenance="live",
            tenant_id="tenant-acme",
            environment="staging",
            status="failed",
            observed_at=now + timedelta(seconds=1),
        )

        projector.ingest_receipt(prod_stim)
        projector.ingest_receipts([staging_stim, staging_term])

        obs_prod = projector.get_observation("r-env", "c-env", 5, tenant_id="tenant-acme", environment="production")
        assert obs_prod is not None
        assert obs_prod.status == "open"
        assert obs_prod.environment == "production"

        obs_staging = projector.get_observation("r-env", "c-env", 5, tenant_id="tenant-acme", environment="staging")
        assert obs_staging is not None
        assert obs_staging.status == "failed"
        assert obs_staging.environment == "staging"

    def test_cross_tenant_shared_receipt_id_allowed_and_within_scope_conflict_rejected(self) -> None:
        """Shared receipt_id across different tenants is valid; within-scope conflict is rejected."""
        store = MemoryTwelveLoopStore()
        projector = TwelveLoopTruthProjector(store=store, all_scopes=True, auto_load=False)
        now = _utc()

        r1 = CanonicalLoopReceipt(
            receipt_id="shared-uuid",
            receipt_type="terminal",
            loop_id=1,
            correlation_id="corr-1",
            release_id="rel-1",
            owner="source-ingest",
            provenance="live",
            tenant_id="tenant-alpha",
            environment="production",
            status="completed",
            observed_at=now,
        )
        r2 = CanonicalLoopReceipt(
            receipt_id="shared-uuid",
            receipt_type="terminal",
            loop_id=1,
            correlation_id="corr-1",
            release_id="rel-1",
            owner="source-ingest",
            provenance="live",
            tenant_id="tenant-beta",
            environment="production",
            status="completed",
            observed_at=now,
        )

        # Ingestion across distinct scopes succeeds (valid cross-scope collision)
        obs1 = projector.ingest_receipt(r1)
        assert obs1.tenant_id == "tenant-alpha"
        obs2 = projector.ingest_receipt(r2)
        assert obs2.tenant_id == "tenant-beta"

        # Within the SAME scope (tenant-alpha, production), conflicting receipt identity is rejected
        r_conflict = CanonicalLoopReceipt(
            receipt_id="shared-uuid",
            receipt_type="stimulus",
            loop_id=1,
            correlation_id="corr-1",
            release_id="rel-1",
            owner="source-ingest",
            provenance="live",
            tenant_id="tenant-alpha",
            environment="production",
            observed_at=now,
        )
        with pytest.raises(ValueError, match="Conflicting receipt identity"):
            projector.ingest_receipt(r_conflict)

        # Two scoped projectors sharing store do not reject each other's stimulus
        p_alpha = TwelveLoopTruthProjector(store=store, tenant_id="tenant-alpha", environment="production")
        p_beta = TwelveLoopTruthProjector(store=store, tenant_id="tenant-beta", environment="production")
        assert p_alpha.get_observation("rel-1", "corr-1", 1) is not None
        assert p_beta.get_observation("rel-1", "corr-1", 1) is not None

        # All-scopes rebuild succeeds without collision
        rebuilt = projector.rebuild()
        assert len(rebuilt) == 2

    def test_projector_scoped_rejects_conflicting_receipt_scope(self) -> None:
        """A projector configured for a specific tenant rejects receipts with mismatched tenant scope."""
        projector = TwelveLoopTruthProjector(tenant_id="tenant-alpha", environment="production")
        now = _utc()

        wrong_tenant_receipt = CanonicalLoopReceipt(
            receipt_id="rcpt-wrong-tenant",
            receipt_type="stimulus",
            loop_id=1,
            correlation_id="c",
            release_id="r",
            owner="owner",
            provenance="live",
            tenant_id="tenant-beta",
            environment="production",
            observed_at=now,
        )
        with pytest.raises(ValueError, match="conflicts with projector scoped tenant"):
            projector.ingest_receipt(wrong_tenant_receipt)

    def test_project_twelve_canonical_loops_tenant_scoping(self) -> None:
        """project_twelve_canonical_loops returns rows strictly matching target tenant and environment."""
        projector = TwelveLoopTruthProjector()
        now = _utc()

        alpha_receipt = CanonicalLoopReceipt(
            receipt_id="alpha-rcpt",
            receipt_type="terminal",
            loop_id=3,
            correlation_id="c-loop",
            release_id="r-loop",
            owner="alpha-rep",
            provenance="live",
            tenant_id="tenant-alpha",
            environment="production",
            status="completed",
            observed_at=now,
        )
        projector.ingest_receipt(alpha_receipt)

        # Project for tenant-alpha
        rows_alpha = projector.project_twelve_canonical_loops("r-loop", "c-loop", tenant_id="tenant-alpha", environment="production")
        assert len(rows_alpha) == 12
        row_3_alpha = next(r for r in rows_alpha if r["loop_id"] == 3)
        assert row_3_alpha["tenant_id"] == "tenant-alpha"
        assert row_3_alpha["environment"] == "production"
        assert row_3_alpha["terminal_id"] == "alpha-rcpt"

        # Project for tenant-beta on same release/correlation: loop 3 must be unobserved
        rows_beta = projector.project_twelve_canonical_loops("r-loop", "c-loop", tenant_id="tenant-beta", environment="production")
        assert len(rows_beta) == 12
        row_3_beta = next(r for r in rows_beta if r["loop_id"] == 3)
        assert row_3_beta["tenant_id"] == "tenant-beta"
        assert row_3_beta["environment"] == "production"
        assert row_3_beta["status"] == "unobserved"
        assert row_3_beta["terminal_id"] is None

    def test_scoped_rebuild_incremental_equivalence_multi_tenant(self) -> None:
        """Incremental ingestion equals full rebuild across multiple tenants and environments."""
        store = MemoryTwelveLoopStore()
        projector = TwelveLoopTruthProjector(store, auto_load=False)
        now = _utc()

        receipts = [
            CanonicalLoopReceipt("t1-s", "stimulus", 1, "c1", "r1", "owner", "live", tenant_id="t1", environment="prod", observed_at=now),
            CanonicalLoopReceipt("t2-s", "stimulus", 1, "c1", "r1", "owner", "live", tenant_id="t2", environment="prod", observed_at=now),
            CanonicalLoopReceipt("t1-t", "terminal", 1, "c1", "r1", "owner", "live", tenant_id="t1", environment="prod", status="completed", observed_at=now + timedelta(seconds=1), causation_id="t1-s"),
            CanonicalLoopReceipt("t2-t", "terminal", 1, "c1", "r1", "owner", "live", tenant_id="t2", environment="prod", status="failed", observed_at=now + timedelta(seconds=1), causation_id="t2-s"),
            CanonicalLoopReceipt("t1-n", "next_consumer", 1, "c1", "r1", "owner", "live", tenant_id="t1", environment="prod", status="accepted", observed_at=now + timedelta(seconds=2), causation_id="t1-t"),
        ]

        for r in receipts:
            projector.ingest_receipt(r)

        incremental_t1 = projector.get_observation("r1", "c1", 1, tenant_id="t1", environment="prod").to_dict()
        incremental_t2 = projector.get_observation("r1", "c1", 1, tenant_id="t2", environment="prod").to_dict()

        # Rebuild from scratch
        projector.rebuild()
        rebuilt_t1 = projector.get_observation("r1", "c1", 1, tenant_id="t1", environment="prod").to_dict()
        rebuilt_t2 = projector.get_observation("r1", "c1", 1, tenant_id="t2", environment="prod").to_dict()

        assert incremental_t1 == rebuilt_t1
        assert incremental_t2 == rebuilt_t2
        assert incremental_t1["status"] == "complete"
        assert incremental_t2["status"] == "failed"

    def test_validate_scope_fail_closed_on_partial_scope(self) -> None:
        """validate_scope fails closed on partial scope or blank strings."""
        from services.control_plane.bff.management_read_models.twelve_loop_projector import validate_scope

        # Fully scoped is valid
        validate_scope("tenant-1", "production")

        # Fully unscoped is valid
        validate_scope(None, None)

        # all_scopes explicitly permitted
        validate_scope(None, None, allow_all_scopes=True)

        # Partial scope fails closed
        with pytest.raises(ValueError, match="Partial caller scope is invalid"):
            validate_scope("tenant-1", None)

        with pytest.raises(ValueError, match="Partial caller scope is invalid"):
            validate_scope(None, "production")

        # Blank strings fail closed
        with pytest.raises(ValueError, match="tenant_id cannot be empty or blank"):
            validate_scope("", "production")

        with pytest.raises(ValueError, match="environment cannot be empty or blank"):
            validate_scope("tenant-1", "   ")

    def test_unscoped_legacy_stimulus_does_not_reduce_with_scoped_receipts_in_memory(self) -> None:
        """Unscoped legacy receipts and scoped receipts on same release/correlation/loop never cross-reduce."""
        store = MemoryTwelveLoopStore()
        projector = TwelveLoopTruthProjector(store, auto_load=False)
        now = _utc()

        # Ingest scoped complete loop
        projector.ingest_receipts([
            CanonicalLoopReceipt("rcpt-s-scoped", "stimulus", 1, "c-cross", "r-cross", "owner", "live", tenant_id="tenant-a", environment="prod", observed_at=now),
            CanonicalLoopReceipt("rcpt-t-scoped", "terminal", 1, "c-cross", "r-cross", "owner", "live", tenant_id="tenant-a", environment="prod", status="completed", observed_at=now + timedelta(seconds=1), causation_id="rcpt-s-scoped"),
            CanonicalLoopReceipt("rcpt-n-scoped", "next_consumer", 1, "c-cross", "r-cross", "owner", "live", tenant_id="tenant-a", environment="prod", status="accepted", observed_at=now + timedelta(seconds=2), causation_id="rcpt-t-scoped"),
        ])

        # Ingest legacy unscoped stimulus
        obs_legacy = projector.ingest_receipt(
            CanonicalLoopReceipt("rcpt-s-legacy", "stimulus", 1, "c-cross", "r-cross", "owner", "live", observed_at=now)
        )

        # Legacy observation MUST NOT pick up the scoped terminal receipt
        assert obs_legacy.tenant_id is None
        assert obs_legacy.environment is None
        assert obs_legacy.status == "open"
        assert obs_legacy.terminal_id is None
        assert obs_legacy.stimulus_id == "rcpt-s-legacy"

        # Scoped observation remains complete
        obs_scoped = projector.get_observation("r-cross", "c-cross", 1, tenant_id="tenant-a", environment="prod")
        assert obs_scoped is not None
        assert obs_scoped.tenant_id == "tenant-a"
        assert obs_scoped.environment == "prod"
        assert obs_scoped.status == "complete"
        assert obs_scoped.terminal_id == "rcpt-t-scoped"

    def test_scoped_projector_rejects_unscoped_and_mismatched_receipts(self) -> None:
        """A projector configured for a specific tenant & env rejects unscoped or mismatched receipts."""
        projector = TwelveLoopTruthProjector(tenant_id="tenant-x", environment="prod")
        now = _utc()

        unscoped = CanonicalLoopReceipt("rcpt-unscoped", "stimulus", 1, "c", "r", "owner", "live", observed_at=now)
        with pytest.raises(ValueError, match="conflicts with projector scoped tenant"):
            projector.ingest_receipt(unscoped)

        mismatched_env = CanonicalLoopReceipt("rcpt-bad-env", "stimulus", 1, "c", "r", "owner", "live", tenant_id="tenant-x", environment="staging", observed_at=now)
        with pytest.raises(ValueError, match="conflicts with projector scoped environment"):
            projector.ingest_receipt(mismatched_env)

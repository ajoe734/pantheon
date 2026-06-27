"""Unit tests for AlphaReplicationQueue."""

from __future__ import annotations

import tempfile

import pytest

from .queue import AlphaReplicationQueue, REVIEWABLE_STATES


def _approved_spec(
    strategy_id: str = "strat-001",
    spec_version: str = "1.0",
    lifecycle_state: str = "approved",
) -> dict:
    return {
        "spec_version": spec_version,
        "strategy_id": strategy_id,
        "lifecycle_state": lifecycle_state,
    }


class TestAlphaReplicationQueueEnqueue:
    def test_enqueue_approved_spec_returns_entry(self, tmp_path):
        q = AlphaReplicationQueue(tmp_path)
        entry = q.enqueue(_approved_spec())
        assert entry is not None
        assert entry["strategy_id"] == "strat-001"
        assert entry["spec_version"] == "1.0"
        assert entry["status"] == "pending"
        assert entry["revalidation_count"] == 0
        assert entry["experiment_run_ids"] == []

    def test_enqueue_review_state_accepted(self, tmp_path):
        q = AlphaReplicationQueue(tmp_path)
        entry = q.enqueue(_approved_spec(lifecycle_state="review"))
        assert entry is not None
        assert entry["lifecycle_state"] == "review"

    def test_enqueue_draft_state_rejected(self, tmp_path):
        q = AlphaReplicationQueue(tmp_path)
        with pytest.raises(ValueError, match="lifecycle_state"):
            q.enqueue(_approved_spec(lifecycle_state="draft"))

    def test_enqueue_candidate_state_rejected(self, tmp_path):
        q = AlphaReplicationQueue(tmp_path)
        with pytest.raises(ValueError, match="lifecycle_state"):
            q.enqueue(_approved_spec(lifecycle_state="candidate"))

    def test_enqueue_missing_strategy_id_raises(self, tmp_path):
        q = AlphaReplicationQueue(tmp_path)
        spec = _approved_spec()
        del spec["strategy_id"]
        with pytest.raises(ValueError, match="strategy_id"):
            q.enqueue(spec)

    def test_enqueue_missing_spec_version_raises(self, tmp_path):
        q = AlphaReplicationQueue(tmp_path)
        spec = _approved_spec()
        del spec["spec_version"]
        with pytest.raises(ValueError, match="spec_version"):
            q.enqueue(spec)

    def test_enqueue_idempotent_same_spec(self, tmp_path):
        q = AlphaReplicationQueue(tmp_path)
        first = q.enqueue(_approved_spec())
        second = q.enqueue(_approved_spec())
        assert first is not None
        assert second is None  # duplicate
        assert len(q.list_all()) == 1

    def test_enqueue_different_versions_both_accepted(self, tmp_path):
        q = AlphaReplicationQueue(tmp_path)
        q.enqueue(_approved_spec(strategy_id="s1", spec_version="1.0"))
        q.enqueue(_approved_spec(strategy_id="s1", spec_version="1.1"))
        assert len(q.list_all()) == 2

    def test_enqueue_different_strategies_both_accepted(self, tmp_path):
        q = AlphaReplicationQueue(tmp_path)
        q.enqueue(_approved_spec(strategy_id="s1"))
        q.enqueue(_approved_spec(strategy_id="s2"))
        assert len(q.list_all()) == 2

    def test_enqueue_records_enqueued_by(self, tmp_path):
        q = AlphaReplicationQueue(tmp_path)
        entry = q.enqueue(_approved_spec(), enqueued_by="distillation-worker")
        assert entry is not None
        assert entry["enqueued_by"] == "distillation-worker"

    def test_enqueued_at_is_set(self, tmp_path):
        q = AlphaReplicationQueue(tmp_path)
        entry = q.enqueue(_approved_spec())
        assert entry is not None
        assert entry["enqueued_at"].endswith("Z")


class TestAlphaReplicationQueueList:
    def test_list_pending_returns_only_pending(self, tmp_path):
        q = AlphaReplicationQueue(tmp_path)
        q.enqueue(_approved_spec(strategy_id="s1"))
        q.enqueue(_approved_spec(strategy_id="s2"))
        pending = q.list_pending()
        assert len(pending) == 2
        assert all(e["status"] == "pending" for e in pending)

    def test_list_all_returns_all_entries(self, tmp_path):
        q = AlphaReplicationQueue(tmp_path)
        q.enqueue(_approved_spec(strategy_id="s1"))
        q.enqueue(_approved_spec(strategy_id="s2"))
        assert len(q.list_all()) == 2

    def test_list_empty_when_no_entries(self, tmp_path):
        q = AlphaReplicationQueue(tmp_path)
        assert q.list_pending() == []
        assert q.list_all() == []


class TestAlphaReplicationQueueMarkRevalidated:
    def test_mark_revalidated_updates_entry(self, tmp_path):
        q = AlphaReplicationQueue(tmp_path)
        q.enqueue(_approved_spec())
        found = q.mark_revalidated("strat-001", "1.0", run_id="run-abc", status="dispatched")
        assert found is True
        entries = q.list_all()
        assert entries[0]["last_revalidation_status"] == "dispatched"
        assert entries[0]["revalidation_count"] == 1
        assert "run-abc" in entries[0]["experiment_run_ids"]

    def test_mark_revalidated_increments_count(self, tmp_path):
        q = AlphaReplicationQueue(tmp_path)
        q.enqueue(_approved_spec())
        q.mark_revalidated("strat-001", "1.0", run_id="run-1", status="dispatched")
        q.mark_revalidated("strat-001", "1.0", run_id="run-2", status="dispatched")
        entry = q.list_all()[0]
        assert entry["revalidation_count"] == 2
        assert set(entry["experiment_run_ids"]) == {"run-1", "run-2"}

    def test_mark_revalidated_returns_false_for_unknown_spec(self, tmp_path):
        q = AlphaReplicationQueue(tmp_path)
        found = q.mark_revalidated("nonexistent", "1.0", run_id="run-x", status="failed")
        assert found is False

    def test_mark_revalidated_does_not_duplicate_run_id(self, tmp_path):
        q = AlphaReplicationQueue(tmp_path)
        q.enqueue(_approved_spec())
        q.mark_revalidated("strat-001", "1.0", run_id="run-1", status="dispatched")
        q.mark_revalidated("strat-001", "1.0", run_id="run-1", status="dispatched")
        entry = q.list_all()[0]
        assert entry["experiment_run_ids"].count("run-1") == 1


class TestAlphaReplicationQueueMetrics:
    def test_metrics_initial_state(self, tmp_path):
        q = AlphaReplicationQueue(tmp_path)
        metrics = q.get_metrics()
        assert metrics["total"] == 0
        assert metrics["pending"] == 0

    def test_metrics_counts_pending(self, tmp_path):
        q = AlphaReplicationQueue(tmp_path)
        q.enqueue(_approved_spec(strategy_id="s1"))
        q.enqueue(_approved_spec(strategy_id="s2"))
        metrics = q.get_metrics()
        assert metrics["total"] == 2
        assert metrics["pending"] == 2
        assert metrics["revalidated"] == 0

    def test_metrics_after_revalidation(self, tmp_path):
        q = AlphaReplicationQueue(tmp_path)
        q.enqueue(_approved_spec(strategy_id="s1"))
        q.mark_revalidated("s1", "1.0", run_id="r1", status="dispatched")
        metrics = q.get_metrics()
        assert metrics["revalidated"] == 1

    def test_metrics_failed_count(self, tmp_path):
        q = AlphaReplicationQueue(tmp_path)
        q.enqueue(_approved_spec(strategy_id="s1"))
        q.mark_revalidated("s1", "1.0", run_id="r1", status="failed")
        metrics = q.get_metrics()
        assert metrics["last_revalidation_failed"] == 1


class TestAlphaReplicationQueuePersistence:
    def test_queue_persists_across_instances(self, tmp_path):
        q1 = AlphaReplicationQueue(tmp_path)
        q1.enqueue(_approved_spec())
        q2 = AlphaReplicationQueue(tmp_path)
        assert len(q2.list_all()) == 1

    def test_rewrite_persists_updated_entries(self, tmp_path):
        q1 = AlphaReplicationQueue(tmp_path)
        q1.enqueue(_approved_spec())
        q1.mark_revalidated("strat-001", "1.0", run_id="r1", status="dispatched")
        q2 = AlphaReplicationQueue(tmp_path)
        entry = q2.list_all()[0]
        assert entry["last_revalidation_status"] == "dispatched"
        assert entry["revalidation_count"] == 1

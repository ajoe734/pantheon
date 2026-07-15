"""Unit tests for AlphaRevalidationWorker."""

from __future__ import annotations

import os
import tempfile
from unittest import mock

import pytest

from .queue import AlphaReplicationQueue
from .revalidation_worker import AlphaRevalidationWorker, SAFE_DISPATCH_MODES


def _approved_spec(strategy_id: str = "strat-001", spec_version: str = "1.0") -> dict:
    return {
        "spec_version": spec_version,
        "strategy_id": strategy_id,
        "lifecycle_state": "approved",
    }


def _make_worker(
    tmp_path,
    dispatch_mode: str = "stub",
) -> tuple[AlphaReplicationQueue, AlphaRevalidationWorker]:
    queue = AlphaReplicationQueue(tmp_path)
    worker = AlphaRevalidationWorker(queue, tmp_path, dispatch_mode=dispatch_mode)
    return queue, worker


class TestAlphaRevalidationWorkerSafetyBoundary:
    def test_stub_dispatch_mode_accepted(self, tmp_path):
        _make_worker(tmp_path, dispatch_mode="stub")  # no error

    def test_handoff_only_dispatch_mode_accepted(self, tmp_path):
        _make_worker(tmp_path, dispatch_mode="handoff_only")

    def test_manual_dispatch_mode_accepted(self, tmp_path):
        _make_worker(tmp_path, dispatch_mode="manual")

    def test_production_dispatch_mode_rejected(self, tmp_path):
        queue = AlphaReplicationQueue(tmp_path)
        with pytest.raises(ValueError, match="fail-closed"):
            AlphaRevalidationWorker(queue, tmp_path, dispatch_mode="production")

    def test_paper_dispatch_mode_rejected(self, tmp_path):
        queue = AlphaReplicationQueue(tmp_path)
        with pytest.raises(ValueError, match="fail-closed"):
            AlphaRevalidationWorker(queue, tmp_path, dispatch_mode="paper")

    def test_live_dispatch_mode_rejected(self, tmp_path):
        queue = AlphaReplicationQueue(tmp_path)
        with pytest.raises(ValueError, match="fail-closed"):
            AlphaRevalidationWorker(queue, tmp_path, dispatch_mode="live")

    def test_env_var_overrides_default(self, tmp_path):
        queue = AlphaReplicationQueue(tmp_path)
        with mock.patch.dict(
            os.environ,
            {"PANTHEON_ALPHA_REVALIDATION_DISPATCH_MODE": "handoff_only"},
        ):
            worker = AlphaRevalidationWorker(queue, tmp_path)
        assert worker._dispatch_mode == "handoff_only"

    def test_env_var_production_rejected(self, tmp_path):
        queue = AlphaReplicationQueue(tmp_path)
        with mock.patch.dict(
            os.environ,
            {"PANTHEON_ALPHA_REVALIDATION_DISPATCH_MODE": "live"},
        ):
            with pytest.raises(ValueError, match="fail-closed"):
                AlphaRevalidationWorker(queue, tmp_path)


class TestAlphaRevalidationWorkerRunOnce:
    def test_empty_queue_produces_no_runs(self, tmp_path):
        _, worker = _make_worker(tmp_path)
        result = worker.run_once()
        assert result["processed"] == 0
        assert result["created_run_ids"] == []
        assert result["errors"] == []

    def test_run_once_creates_stub_run_for_pending_entry(self, tmp_path):
        queue, worker = _make_worker(tmp_path)
        queue.enqueue(_approved_spec("s1"))
        result = worker.run_once()
        assert result["processed"] == 1
        assert len(result["created_run_ids"]) == 1
        assert result["dispatch_mode"] == "stub"

    def test_run_once_idempotent_duplicate_tick(self, tmp_path):
        queue, worker = _make_worker(tmp_path)
        queue.enqueue(_approved_spec("s1"))
        worker.run_once()
        result2 = worker.run_once()
        # Second tick: already processed, returns skipped run_id
        assert result2["processed"] == 1
        assert len(result2["created_run_ids"]) == 1
        runs = worker.list_runs()
        assert len(runs) == 1  # no duplicate run created

    def test_run_once_processes_multiple_pending_entries(self, tmp_path):
        queue, worker = _make_worker(tmp_path)
        queue.enqueue(_approved_spec("s1"))
        queue.enqueue(_approved_spec("s2"))
        queue.enqueue(_approved_spec("s3"))
        result = worker.run_once()
        assert result["processed"] == 3
        assert len(result["created_run_ids"]) == 3
        runs = worker.list_runs()
        assert len(runs) == 3

    def test_run_record_has_required_fields(self, tmp_path):
        queue, worker = _make_worker(tmp_path)
        queue.enqueue(_approved_spec("s1"))
        worker.run_once()
        runs = worker.list_runs()
        run = runs[0]
        assert run["run_id"].startswith("arvrun-")
        assert run["task_id"].startswith("arvtask-")
        assert run["strategy_id"] == "s1"
        assert run["spec_version"] == "1.0"
        assert run["status"] == "pending"
        assert run["runtime_env"] == "research"
        assert run["production_activation"] == "disabled"
        assert run["dispatch_mode"] == "stub"
        assert run["trace_id"].startswith("trace-alpha-reval-")

    def test_run_record_persists_to_file(self, tmp_path):
        queue, worker = _make_worker(tmp_path)
        queue.enqueue(_approved_spec("s1"))
        worker.run_once()
        worker2 = AlphaRevalidationWorker(queue, tmp_path)
        runs = worker2.list_runs()
        assert len(runs) == 1

    def test_run_queue_entry_updated_after_dispatch(self, tmp_path):
        queue, worker = _make_worker(tmp_path)
        queue.enqueue(_approved_spec("s1"))
        worker.run_once()
        entries = queue.list_all()
        assert entries[0]["last_revalidation_status"] in ("dispatched", "skipped_already_exists")
        assert entries[0]["revalidation_count"] >= 1
        assert len(entries[0]["experiment_run_ids"]) >= 1

    def test_run_once_non_stub_revalidation_completed(self, tmp_path):
        queue, worker = _make_worker(tmp_path, dispatch_mode="handoff_only")
        queue.enqueue(_approved_spec("s1"))
        
        mock_spec = {
            "spec_version": "1.0",
            "strategy_id": "s1",
            "title": "Mock Canonical Strategy s1",
            "hypothesis": "Two liquid symbols SMA crossover produces a research signal.",
            "objective": "Prove revalidation.",
            "lifecycle_state": "candidate",
            "market_scope": {
                "symbols": ["SPY"],
                "asset_classes": ["equity"],
                "frequency": "1d",
                "venues": ["NYSE"]
            },
            "data_dependencies": [
                {"ref": "dataset:synthetic", "kind": "dataset"}
            ],
            "execution_profile": {
                "signal_schema_version": "1.0",
                "quantity_type": "PERCENT_PORTFOLIO",
                "rebalance_cadence": "1d",
                "execution_mode_hint": "research"
            },
            "evaluation_plan": {
                "metrics": ["sharpe_ratio"],
                "candidate_gate": "Gate pass.",
                "paper_gate": "Paper gate.",
                "live_gate": "Live gate."
            },
            "governance": {
                "approval_required": True,
                "policy_id": "policy-1",
                "risk_profile": "research_only"
            },
            "provenance": {
                "source_kind": "workflow",
                "created_at": "2026-05-17T11:10:00Z",
                "source_refs": ["source:1"],
                "created_by": "Codex"
            }
        }
        
        with mock.patch.object(worker, "_fetch_spec_from_registry", return_value=mock_spec) as mock_fetch:
            result = worker.run_once()
            mock_fetch.assert_called_once_with("s1", "1.0")

        assert result["processed"] == 1
        assert len(result["created_run_ids"]) == 1
        assert result["dispatch_mode"] == "handoff_only"

        runs = worker.list_runs()
        assert len(runs) == 1
        run = runs[0]
        assert run["status"] == "completed"
        assert run["started_at"] is not None
        assert run["finished_at"] is not None
        assert run["output_manifest_ref"].startswith("alpha-replication://")
        assert run["artifact_refs"] == ["reg-strategy-spec-s1"]
        assert run["metadata"].get("input_source") == "registry"
        assert run["production_activation"] == "disabled"

    def test_run_once_non_stub_revalidation_registry_miss(self, tmp_path):
        queue, worker = _make_worker(tmp_path, dispatch_mode="handoff_only")
        queue.enqueue(_approved_spec("s1"))

        with mock.patch.object(worker, "_fetch_spec_from_registry", return_value=None) as mock_fetch:
            result = worker.run_once()
            mock_fetch.assert_called_once_with("s1", "1.0")

        assert result["processed"] == 1
        assert len(result["created_run_ids"]) == 1
        assert result["dispatch_mode"] == "handoff_only"

        runs = worker.list_runs()
        assert len(runs) == 1
        run = runs[0]
        assert run["status"] == "failed"
        assert run["failure_reason"] == "Registry fetch failed, using synthetic fallback spec"
        assert run["metadata"].get("input_source") == "synthetic"
        assert run["production_activation"] == "disabled"


class TestAlphaRevalidationWorkerMetrics:
    def test_metrics_initial_state(self, tmp_path):
        _, worker = _make_worker(tmp_path)
        m = worker.get_metrics()
        assert m["run_count"] == 0
        assert m["error_count"] == 0
        assert m["last_success_at"] is None

    def test_metrics_updated_after_successful_run(self, tmp_path):
        queue, worker = _make_worker(tmp_path)
        queue.enqueue(_approved_spec("s1"))
        worker.run_once()
        m = worker.get_metrics()
        assert m["run_count"] == 1
        assert m["last_success_at"] is not None
        assert m["last_failure_at"] is None

    def test_metrics_persist_across_instances(self, tmp_path):
        queue, worker1 = _make_worker(tmp_path)
        queue.enqueue(_approved_spec("s1"))
        worker1.run_once()
        queue2 = AlphaReplicationQueue(tmp_path)
        worker2 = AlphaRevalidationWorker(queue2, tmp_path)
        m = worker2.get_metrics()
        assert m["run_count"] == 1

    def test_metrics_multiple_ticks_accumulate(self, tmp_path):
        queue, worker = _make_worker(tmp_path)
        queue.enqueue(_approved_spec("s1"))
        queue.enqueue(_approved_spec("s2"))
        worker.run_once()
        m = worker.get_metrics()
        assert m["run_count"] == 2


class TestAlphaRevalidationWorkerListRuns:
    def test_list_runs_empty_initially(self, tmp_path):
        _, worker = _make_worker(tmp_path)
        assert worker.list_runs() == []

    def test_list_runs_by_strategy_id_filters(self, tmp_path):
        queue, worker = _make_worker(tmp_path)
        queue.enqueue(_approved_spec("s1"))
        queue.enqueue(_approved_spec("s2"))
        worker.run_once()
        runs = worker.list_runs(strategy_id="s1")
        assert len(runs) == 1
        assert runs[0]["strategy_id"] == "s1"

    def test_list_runs_unknown_strategy_id_returns_empty(self, tmp_path):
        queue, worker = _make_worker(tmp_path)
        queue.enqueue(_approved_spec("s1"))
        worker.run_once()
        assert worker.list_runs(strategy_id="nonexistent") == []

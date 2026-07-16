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
        # Second tick: already processed, leaves pending
        assert result2["processed"] == 0
        assert len(result2["created_run_ids"]) == 0
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
        assert run["strategy_spec_version"] == "1.0"
        assert run["status"] == "pending"
        assert run["runtime_env"] == "research"
        assert run["metadata"]["production_activation"] == "disabled"
        assert run["metadata"]["dispatch_mode"] == "stub"
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
        
        with mock.patch.object(worker, "_fetch_spec_from_registry", return_value=mock_spec) as mock_fetch, \
             mock.patch.object(worker, "_writeback_lineage_to_registry") as mock_writeback:
            result = worker.run_once()
            mock_fetch.assert_called_once_with("s1", "1.0")
            mock_writeback.assert_called_once_with("s1", "1.0", mock.ANY, "dataset:synthetic", mock.ANY)

        assert result["processed"] == 1
        assert len(result["created_run_ids"]) == 1
        assert result["dispatch_mode"] == "handoff_only"

        runs = worker.list_runs()
        assert len(runs) == 1
        run = runs[0]
        assert run["status"] == "completed"
        assert run["started_at"] is not None
        assert run["finished_at"] is not None
        assert run["output_manifest_ref"].startswith("manifest://")
        assert run["artifact_refs"] == ["reg-strategy-spec-s1"]
        assert run["metadata"].get("input_source") == "registry"
        assert run["metadata"].get("production_activation") == "disabled"

    def test_run_once_non_stub_revalidation_registry_miss(self, tmp_path):
        queue, worker = _make_worker(tmp_path, dispatch_mode="handoff_only")
        queue.enqueue(_approved_spec("s1"))

        with mock.patch.object(worker, "_fetch_spec_from_registry", return_value=None) as mock_fetch:
            result = worker.run_once()
            mock_fetch.assert_called_once_with("s1", "1.0")

        assert result["processed"] == 1
        assert len(result["created_run_ids"]) == 0
        assert len(result["errors"]) == 1
        assert "Stale, retired, or missing StrategySpec" in result["errors"][0]["error"]
        
        runs = worker.list_runs()
        assert len(runs) == 1
        run = runs[0]
        assert run["status"] == "failed"
        assert "Stale, retired, or missing StrategySpec" in run["failure_reason"]
        assert run["metadata"].get("production_activation") == "disabled"


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


class TestAlphaRevalidationWorkerBehavioralProof:
    def test_restart_and_recovery_flow(self, tmp_path):
        # 1. Start with initial queue and worker
        queue = AlphaReplicationQueue(tmp_path)
        worker = AlphaRevalidationWorker(queue, tmp_path)
        
        # Enqueue spec
        spec = _approved_spec("strat-restart", "1.0")
        queue.enqueue(spec)
        
        # Verify pending
        assert len(queue.list_pending()) == 1
        
        # 2. Simulate shutdown/restart by recreating queue and worker from the same files
        queue_restarted = AlphaReplicationQueue(tmp_path)
        worker_restarted = AlphaRevalidationWorker(queue_restarted, tmp_path)
        
        # State should be recovered
        assert len(queue_restarted.list_pending()) == 1
        
        # Process and verify
        res = worker_restarted.run_once()
        assert res["processed"] == 1
        assert len(res["created_run_ids"]) == 1
        
        # Recreated worker should list the run
        runs = worker_restarted.list_runs()
        assert len(runs) == 1
        assert runs[0]["strategy_id"] == "strat-restart"
        
        # Recreated worker metrics should be updated
        metrics = worker_restarted.get_metrics()
        assert metrics["run_count"] == 1
        assert metrics["last_success_at"] is not None

    def test_retry_and_recovery_from_exception(self, tmp_path):
        # Test that if _process_entry raises an exception, the queue entry remains pending
        # and can be retried in a subsequent run/after restart (guaranteeing RPO=0 / no lost events).
        queue = AlphaReplicationQueue(tmp_path)
        worker = AlphaRevalidationWorker(queue, tmp_path, dispatch_mode="handoff_only")
        
        # Enqueue spec
        spec = _approved_spec("strat-retry", "1.0")
        queue.enqueue(spec)
        
        # Mock _fetch_spec_from_registry to raise an exception
        with mock.patch.object(worker, "_fetch_spec_from_registry", side_effect=RuntimeError("Transient registry error")):
            result = worker.run_once()
            
            # The run failed with an error
            assert result["processed"] == 1
            assert len(result["created_run_ids"]) == 0
            assert len(result["errors"]) == 1
            assert "Transient registry error" in result["errors"][0]["error"]
            
        # Verify that queue entry is STILL pending (rollback/compensation behavior)
        # and not lost or marked as completed/dispatched.
        assert len(queue.list_pending()) == 1
        
        # Now simulate a retry where the error is resolved
        mock_spec = {
            "spec_version": "1.0",
            "strategy_id": "strat-retry",
            "title": "Strategy",
            "hypothesis": "H",
            "objective": "O",
            "lifecycle_state": "candidate",
            "market_scope": {"symbols": ["SPY"], "asset_classes": ["equity"], "frequency": "1d", "venues": ["NYSE"]},
            "data_dependencies": [{"ref": "dataset:synthetic", "kind": "dataset"}],
            "execution_profile": {"signal_schema_version": "1.0", "quantity_type": "PERCENT_PORTFOLIO", "rebalance_cadence": "1d", "execution_mode_hint": "research"},
            "evaluation_plan": {"metrics": ["sharpe_ratio"], "candidate_gate": "pass", "paper_gate": "pass", "live_gate": "pass"},
            "governance": {"approval_required": True, "policy_id": "policy-1", "risk_profile": "research_only"},
            "provenance": {"source_kind": "workflow", "created_at": "2026-05-17T11:10:00Z", "source_refs": ["source:1"], "created_by": "Codex"}
        }
        with mock.patch.object(worker, "_fetch_spec_from_registry", return_value=mock_spec), \
             mock.patch.object(worker, "_writeback_lineage_to_registry") as mock_writeback:
            result2 = worker.run_once()
            assert result2["processed"] == 1
            assert len(result2["created_run_ids"]) == 1
            
        # The queue entry is still logically there, but its last revalidation status is completed.
        entries = queue.list_all()
        assert len(entries) == 1
        assert entries[0]["last_revalidation_status"] == "completed"

    def test_run_once_tenant_scoped_claiming(self, tmp_path):
        queue, worker = _make_worker(tmp_path)
        
        # Enqueue spec for tenant A
        spec_a = _approved_spec("s-a")
        spec_a["tenant_id"] = "tenant-a"
        queue.enqueue(spec_a)
        
        # Enqueue spec for tenant B
        spec_b = _approved_spec("s-b")
        spec_b["tenant_id"] = "tenant-b"
        queue.enqueue(spec_b)
        
        # Run for tenant-a only
        result_a = worker.run_once(tenant_id="tenant-a")
        assert result_a["processed"] == 1
        assert len(result_a["created_run_ids"]) == 1
        assert result_a["created_run_ids"][0].startswith("arvrun-s-a-")
        
        # Run for tenant-b only
        result_b = worker.run_once(tenant_id="tenant-b")
        assert result_b["processed"] == 1
        assert len(result_b["created_run_ids"]) == 1
        assert result_b["created_run_ids"][0].startswith("arvrun-s-b-")

    def test_worker_replay_dlq(self, tmp_path):
        queue, worker = _make_worker(tmp_path)
        spec = _approved_spec("s1")
        queue.enqueue(spec)
        
        # Fail it 3 times to move to DLQ
        for _ in range(3):
            queue.mark_failed("s1", "1.0", error="error", max_retries=3)
            
        entries = queue.list_all()
        assert entries[0]["status"] == "dlq"
        
        # Replay via worker
        assert worker.replay_dlq("s1", "1.0") is True
        
        entries = queue.list_all()
        assert entries[0]["status"] == "pending"

    def test_non_stub_writeback_lineage_fail_closed_and_readback(self, tmp_path):
        import json
        queue, worker = _make_worker(tmp_path, dispatch_mode="handoff_only")
        
        # 1. Test success (POST returns registry_id, GET returns correct producer_run_id)
        mock_response_post = mock.MagicMock()
        mock_response_post.__enter__.return_value = mock_response_post
        mock_response_post.read.return_value = json.dumps({"entry": {"registry_id": "reg-1"}}).encode("utf-8")
        
        mock_response_get = mock.MagicMock()
        mock_response_get.__enter__.return_value = mock_response_get
        mock_response_get.read.return_value = json.dumps({"entry": {"producer_run_id": "run-1"}}).encode("utf-8")
        
        with mock.patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = [mock_response_post, mock_response_get]
            
            # This should run without raising an error
            worker._writeback_lineage_to_registry("s1", "1.0", "run-1", "dataset-1", "code-1")
            
            assert mock_urlopen.call_count == 2
            
        # 2. Test readback mismatch (GET returns wrong producer_run_id)
        mock_response_get_mismatch = mock.MagicMock()
        mock_response_get_mismatch.__enter__.return_value = mock_response_get_mismatch
        mock_response_get_mismatch.read.return_value = json.dumps({"entry": {"producer_run_id": "run-mismatch"}}).encode("utf-8")
        
        with mock.patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = [mock_response_post, mock_response_get_mismatch]
            
            with pytest.raises(RuntimeError, match="Readback verification failed"):
                worker._writeback_lineage_to_registry("s1", "1.0", "run-1", "dataset-1", "code-1")

        # 3. Test HTTP failure (fails closed / raises RuntimeError)
        with mock.patch("urllib.request.urlopen", side_effect=Exception("HTTP connection failed")):
            with pytest.raises(RuntimeError, match="Lineage writeback or readback verification failed"):
                worker._writeback_lineage_to_registry("s1", "1.0", "run-1", "dataset-1", "code-1")


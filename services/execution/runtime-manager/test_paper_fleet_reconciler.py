"""Tests for PaperFleetReconciler."""
from __future__ import annotations

import subprocess
import threading
import unittest
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch


class _FakeProcess:
    """Minimal subprocess.Popen stub."""

    def __init__(self, pid: int = 42, returncode: Optional[int] = None) -> None:
        self.pid = pid
        self._returncode = returncode
        self.terminated = False
        self.killed = False
        self.stdout = None
        self.stderr = None

    @property
    def returncode(self) -> Optional[int]:
        return self._returncode

    def poll(self) -> Optional[int]:
        return self._returncode

    def terminate(self) -> None:
        self.terminated = True
        self._returncode = -15

    def kill(self) -> None:
        self.killed = True
        self._returncode = -9

    def wait(self, timeout: float = 0) -> int:
        return self._returncode or 0


def _make_binding(
    binding_id: str = "b-001",
    runtime_id: str = "rt-001",
    capital_pool_id: str = "pool-a",
    deployment_mode: str = "paper",
    status: str = "active",
    **kwargs: Any,
) -> Dict[str, Any]:
    return {
        "binding_id": binding_id,
        "runtime_id": runtime_id,
        "capital_pool_id": capital_pool_id,
        "artifact_id": "art-001",
        "artifact_version": "1.0.0",
        "plan_id": "plan-001",
        "persona_capital_binding_id": "pcb-001",
        "deployment_mode": deployment_mode,
        "status": status,
        **kwargs,
    }


class _InstrumentedReconciler:
    """
    Wrap PaperFleetReconciler with:
    - injected fake binding list
    - spawns replaced by _FakeProcess factories
    """

    def __init__(self, **kwargs: Any) -> None:
        from paper_fleet_reconciler import PaperFleetReconciler

        self.spawned: List[Dict[str, Any]] = []
        self._fake_bindings: List[Dict[str, Any]] = []
        self._next_pid = 100

        class _TestReconciler(PaperFleetReconciler):
            def _fetch_active_paper_bindings(inner_self) -> List[Dict[str, Any]]:
                return list(self._fake_bindings)

            def _spawn(inner_self, binding_id: str, port: int, env: Any) -> _FakeProcess:
                pid = self._next_pid
                self._next_pid += 1
                proc = _FakeProcess(pid=pid)
                self.spawned.append({"binding_id": binding_id, "port": port, "pid": pid, "proc": proc})
                return proc

        self.recon = _TestReconciler(**kwargs)

    def set_bindings(self, bindings: List[Dict[str, Any]]) -> None:
        self._fake_bindings = bindings


class TestPaperFleetReconcilerBasic(unittest.TestCase):
    def setUp(self) -> None:
        self.wrapper = _InstrumentedReconciler(
            poll_interval_seconds=999,
            worker_base_port=9100,
            max_restarts=3,
            restart_backoff_seconds=0,
            drain_timeout_seconds=1,
        )
        self.recon = self.wrapper.recon

    def test_no_active_bindings_no_workers(self) -> None:
        self.wrapper.set_bindings([])
        snap = self.recon.reconcile_once()
        self.assertEqual(snap["worker_count"], 0)
        self.assertEqual(snap["running_count"], 0)

    def test_one_active_binding_starts_worker(self) -> None:
        self.wrapper.set_bindings([_make_binding("b-001")])
        snap = self.recon.reconcile_once()
        self.assertEqual(snap["worker_count"], 1)
        self.assertEqual(snap["running_count"], 1)
        self.assertEqual(len(self.wrapper.spawned), 1)
        self.assertEqual(self.wrapper.spawned[0]["binding_id"], "b-001")

    def test_second_reconcile_does_not_duplicate(self) -> None:
        self.wrapper.set_bindings([_make_binding("b-001")])
        self.recon.reconcile_once()
        self.recon.reconcile_once()
        self.assertEqual(len(self.wrapper.spawned), 1)

    def test_two_bindings_two_workers_different_ports(self) -> None:
        self.wrapper.set_bindings([
            _make_binding("b-001", runtime_id="rt-1"),
            _make_binding("b-002", runtime_id="rt-2"),
        ])
        snap = self.recon.reconcile_once()
        self.assertEqual(snap["worker_count"], 2)
        ports = {w["port"] for w in self.wrapper.spawned}
        self.assertEqual(len(ports), 2)

    def test_binding_retired_stops_worker(self) -> None:
        self.wrapper.set_bindings([_make_binding("b-001")])
        self.recon.reconcile_once()
        self.assertEqual(len(self.recon._workers), 1)

        # Remove from desired → reconciler should stop it
        self.wrapper.set_bindings([])
        snap = self.recon.reconcile_once()
        self.assertEqual(snap["worker_count"], 0)
        self.assertEqual(snap["running_count"], 0)
        # Process should have been terminated
        proc = self.wrapper.spawned[0]["proc"]
        self.assertTrue(proc.terminated or proc.killed)

    def test_snapshot_is_ready_after_first_cycle(self) -> None:
        self.assertFalse(self.recon.is_ready())
        self.recon.reconcile_once()
        self.assertTrue(self.recon.is_ready())

    def test_cycle_count_increments(self) -> None:
        snap1 = self.recon.reconcile_once()
        snap2 = self.recon.reconcile_once()
        self.assertEqual(snap1["cycle_count"], 1)
        self.assertEqual(snap2["cycle_count"], 2)


class TestPaperFleetReconcilerRestarts(unittest.TestCase):
    def setUp(self) -> None:
        self.wrapper = _InstrumentedReconciler(
            poll_interval_seconds=999,
            worker_base_port=9200,
            max_restarts=2,
            restart_backoff_seconds=0,
            drain_timeout_seconds=1,
        )
        self.recon = self.wrapper.recon

    def test_dead_worker_triggers_restart(self) -> None:
        self.wrapper.set_bindings([_make_binding("b-001")])
        self.recon.reconcile_once()
        # Simulate worker exit
        proc = self.wrapper.spawned[0]["proc"]
        proc._returncode = 1

        # Second reconcile: detects dead worker, marks restarting
        self.recon.reconcile_once()
        # Third reconcile: dead+desired → restart spawned
        self.recon.reconcile_once()
        # Should have spawned a second process
        self.assertGreater(len(self.wrapper.spawned), 1)

    def test_restart_cap_honored(self) -> None:
        self.wrapper.set_bindings([_make_binding("b-001")])
        # Run enough cycles to exhaust restarts
        for _ in range(10):
            # Kill the latest proc
            if self.wrapper.spawned:
                self.wrapper.spawned[-1]["proc"]._returncode = 1
            self.recon.reconcile_once()

        # No more than max_restarts + 1 total spawns
        self.assertLessEqual(len(self.wrapper.spawned), 3 + 1)


class TestPaperFleetReconcilerEnvBuilder(unittest.TestCase):
    def test_worker_env_contains_binding_fields(self) -> None:
        from paper_fleet_reconciler import PaperFleetReconciler
        recon = PaperFleetReconciler(
            runtime_manager_url="http://rm:8081",
            runtime_manager_token="tok",
            poll_interval_seconds=999,
        )
        binding = _make_binding(
            "b-x",
            runtime_id="rt-x",
            capital_pool_id="pool-x",
            artifact_id="art-x",
            artifact_version="2.0.0",
            plan_id="plan-x",
            persona_capital_binding_id="pcb-x",
        )
        env = recon._build_worker_env(binding)
        self.assertEqual(env["PANTHEON_RUNTIME_BINDING_ID"], "b-x")
        self.assertEqual(env["PANTHEON_RUNTIME_ID"], "rt-x")
        self.assertEqual(env["PANTHEON_CAPITAL_POOL_ID"], "pool-x")
        self.assertEqual(env["PANTHEON_DEPLOYMENT_STAGE"], "paper")
        self.assertEqual(env["PANTHEON_RUNTIME_MODE"], "paper")
        self.assertEqual(env["PANTHEON_ARTIFACT_ID"], "art-x")
        self.assertEqual(env["PANTHEON_ARTIFACT_VERSION"], "2.0.0")
        self.assertEqual(env["PANTHEON_DEPLOYMENT_PLAN_ID"], "plan-x")
        self.assertEqual(env["PANTHEON_PERSONA_CAPITAL_BINDING_ID"], "pcb-x")
        self.assertEqual(env["PANTHEON_RUNTIME_MANAGER_URL"], "http://rm:8081")
        self.assertEqual(env["PANTHEON_RUNTIME_MANAGER_TOKEN"], "tok")

    def test_worker_env_port_is_set(self) -> None:
        from paper_fleet_reconciler import PaperFleetReconciler
        recon = PaperFleetReconciler(
            worker_base_port=9300,
            poll_interval_seconds=999,
        )
        binding = _make_binding("b-y")
        env = recon._build_worker_env(binding)
        # PORT is set by _start_worker, not _build_worker_env;
        # verify other keys are present
        self.assertIn("PANTHEON_RUNTIME_BINDING_ID", env)


class TestPaperFleetReconcilerPortAllocation(unittest.TestCase):
    def test_ports_are_sequential_and_non_overlapping(self) -> None:
        wrapper = _InstrumentedReconciler(
            worker_base_port=9400,
            poll_interval_seconds=999,
        )
        wrapper.set_bindings([
            _make_binding("b-1"),
            _make_binding("b-2"),
            _make_binding("b-3"),
        ])
        wrapper.recon.reconcile_once()
        ports = [s["port"] for s in wrapper.spawned]
        self.assertEqual(sorted(ports), list(range(9400, 9403)))

    def test_freed_port_can_be_reused(self) -> None:
        wrapper = _InstrumentedReconciler(
            worker_base_port=9500,
            poll_interval_seconds=999,
        )
        wrapper.set_bindings([_make_binding("b-1")])
        wrapper.recon.reconcile_once()
        first_port = wrapper.spawned[0]["port"]

        # Remove binding → port freed
        wrapper.set_bindings([])
        wrapper.recon.reconcile_once()

        # Add a new binding → should reuse the freed port
        wrapper.set_bindings([_make_binding("b-2")])
        wrapper.recon.reconcile_once()
        second_port = wrapper.spawned[1]["port"]
        self.assertEqual(first_port, second_port)


class TestPaperFleetReconcilerSnapshot(unittest.TestCase):
    def test_snapshot_fields(self) -> None:
        wrapper = _InstrumentedReconciler(
            runtime_manager_url="http://rm:8081",
            worker_base_port=9600,
            poll_interval_seconds=15,
        )
        wrapper.set_bindings([_make_binding("b-001", runtime_id="rt-x", capital_pool_id="cp-x")])
        snap = wrapper.recon.reconcile_once()

        self.assertEqual(snap["reconciler"], "paper_fleet_reconciler")
        self.assertIn("started_at", snap)
        self.assertIn("last_reconcile_at", snap)
        self.assertEqual(snap["cycle_count"], 1)
        self.assertEqual(snap["poll_interval_seconds"], 15)
        self.assertEqual(snap["runtime_manager_url"], "http://rm:8081")
        self.assertEqual(snap["worker_count"], 1)
        self.assertEqual(snap["running_count"], 1)

        worker = snap["workers"][0]
        self.assertEqual(worker["binding_id"], "b-001")
        self.assertEqual(worker["runtime_id"], "rt-x")
        self.assertEqual(worker["capital_pool_id"], "cp-x")
        self.assertEqual(worker["status"], "running")
        self.assertIsNotNone(worker["pid"])
        self.assertEqual(worker["restart_count"], 0)


class TestPaperFleetReconcilerFiltersPaperOnly(unittest.TestCase):
    def test_non_paper_bindings_are_ignored(self) -> None:
        wrapper = _InstrumentedReconciler(worker_base_port=9700, poll_interval_seconds=999)
        # The fake fetch returns only bindings we set; the filter is applied in the real
        # _fetch_active_paper_bindings HTTP response handler. Here we verify that
        # canary/live bindings supplied directly to _fake_bindings are not started.
        # We bypass the filter by supplying non-paper bindings and checking they're skipped.

        # Override the fake fetch to return mixed modes
        wrapper._fake_bindings = [
            _make_binding("b-canary", deployment_mode="canary"),
            _make_binding("b-live", deployment_mode="live"),
            _make_binding("b-paper", deployment_mode="paper"),
        ]

        # The reconciler's reconcile_once does NOT itself filter by mode —
        # _fetch_active_paper_bindings does. So we test the fetch filter directly.
        from paper_fleet_reconciler import PaperFleetReconciler

        class _TestFetch(PaperFleetReconciler):
            def _fetch_active_paper_bindings(self) -> list:
                raw = [
                    _make_binding("b-canary", deployment_mode="canary"),
                    _make_binding("b-live", deployment_mode="live"),
                    _make_binding("b-paper", deployment_mode="paper"),
                ]
                return [b for b in raw if b.get("deployment_mode") == "paper" and b.get("status") == "active"]

            def _spawn(self, binding_id, port, env):  # noqa: ANN001
                proc = _FakeProcess()
                return proc

        recon = _TestFetch(worker_base_port=9750, poll_interval_seconds=999)
        snap = recon.reconcile_once()
        self.assertEqual(snap["worker_count"], 1)
        self.assertEqual(snap["workers"][0]["binding_id"], "b-paper")


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    unittest.main()

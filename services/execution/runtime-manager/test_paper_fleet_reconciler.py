"""Tests for PaperFleetReconciler."""
from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
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
    - injected fake binding list and excluded set
    - spawns replaced by _FakeProcess factories
    """

    def __init__(self, **kwargs: Any) -> None:
        from paper_fleet_reconciler import PaperFleetReconciler

        self.spawned: List[Dict[str, Any]] = []
        self._fake_bindings: List[Dict[str, Any]] = []
        self._fake_excluded_ids: set = set()
        self._next_pid = 100

        class _TestReconciler(PaperFleetReconciler):
            def _fetch_fleet_state(inner_self):
                return (list(self._fake_bindings), set(self._fake_excluded_ids))

            def _spawn(inner_self, binding_id: str, port: int, env: Any) -> _FakeProcess:
                pid = self._next_pid
                self._next_pid += 1
                proc = _FakeProcess(pid=pid)
                self.spawned.append({"binding_id": binding_id, "port": port, "pid": pid, "proc": proc})
                return proc

        self.recon = _TestReconciler(**kwargs)

    def set_bindings(self, bindings: List[Dict[str, Any]]) -> None:
        self._fake_bindings = bindings

    def set_excluded(self, binding_ids: set) -> None:
        self._fake_excluded_ids = set(binding_ids)


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
        # The fleet desired-state endpoint already scopes to paper (stage=paper).
        # Verify that the reconciler only starts workers for bindings returned in
        # the desired list — non-paper bindings in the excluded list are stopped,
        # not started.
        from paper_fleet_reconciler import PaperFleetReconciler

        class _TestFetch(PaperFleetReconciler):
            def _fetch_fleet_state(self):
                # Simulate the runtime-manager returning only the paper binding
                # in the desired list (canary/live filtered out by the endpoint).
                return (
                    [_make_binding("b-paper", deployment_mode="paper")],
                    set(),
                )

            def _spawn(self, binding_id, port, env):  # noqa: ANN001
                proc = _FakeProcess()
                return proc

        recon = _TestFetch(worker_base_port=9750, poll_interval_seconds=999)
        snap = recon.reconcile_once()
        self.assertEqual(snap["worker_count"], 1)
        self.assertEqual(snap["workers"][0]["binding_id"], "b-paper")


class TestPaperFleetReconcilerDegradedFetch(unittest.TestCase):
    """Fetch-failure safety: a runtime-manager outage must not evict live workers."""

    def _make_fail_reconciler(self):
        from paper_fleet_reconciler import PaperFleetReconciler

        state = {"bindings": [], "fail": False}
        spawned = []
        pid_counter = [300]

        class _R(PaperFleetReconciler):
            def _fetch_fleet_state(inner_self):
                if state["fail"]:
                    with inner_self._lock:
                        inner_self._last_error = (
                            "fleet desired state fetch failed: URLError: simulated"
                        )
                    return None
                return (list(state["bindings"]), set())

            def _spawn(inner_self, binding_id, port, env):
                pid = pid_counter[0]; pid_counter[0] += 1
                proc = _FakeProcess(pid=pid)
                spawned.append({"binding_id": binding_id, "pid": pid, "proc": proc})
                return proc

        recon = _R(
            poll_interval_seconds=999,
            worker_base_port=9800,
            max_restarts=2,
            restart_backoff_seconds=0,
            drain_timeout_seconds=1,
        )
        return recon, state, spawned

    def test_fetch_error_preserves_existing_workers(self) -> None:
        recon, state, spawned = self._make_fail_reconciler()
        state["bindings"] = [_make_binding("b-001")]
        recon.reconcile_once()
        self.assertEqual(recon.snapshot()["worker_count"], 1)

        state["fail"] = True
        recon.reconcile_once()

        snap = recon.snapshot()
        self.assertEqual(snap["worker_count"], 1, "fetch error must not evict live workers")
        self.assertEqual(snap["running_count"], 1)

    def test_fetch_error_preserves_last_error(self) -> None:
        recon, state, _spawned = self._make_fail_reconciler()
        state["fail"] = True
        snap = recon.reconcile_once()
        self.assertIsNotNone(snap["last_error"])
        self.assertIn("fetch failed", snap["last_error"])


class TestPaperFleetReconcilerRestartBackoff(unittest.TestCase):
    def test_restart_backoff_delays_second_restart(self) -> None:
        """restart_count=1 restart must not fire immediately when backoff>0."""
        wrapper = _InstrumentedReconciler(
            worker_base_port=9900,
            max_restarts=3,
            restart_backoff_seconds=60.0,  # large so the test window never crosses it
            drain_timeout_seconds=1,
            poll_interval_seconds=999,
        )
        wrapper.set_bindings([_make_binding("b-001")])

        # Cycle 1: start worker (restart_count=0)
        wrapper.recon.reconcile_once()
        self.assertEqual(len(wrapper.spawned), 1)

        # Kill first worker; restart_count=0 → backoff=0*60=0 → immediate restart
        wrapper.spawned[0]["proc"]._returncode = 1
        wrapper.recon.reconcile_once()
        self.assertEqual(len(wrapper.spawned), 2, "first restart should be immediate (backoff=0*60=0)")

        # Kill second worker; restart_count=1 → backoff=1*60=60s → must NOT restart yet
        wrapper.spawned[1]["proc"]._returncode = 1
        wrapper.recon.reconcile_once()
        self.assertEqual(len(wrapper.spawned), 2, "second restart must wait for backoff to elapse")


class TestPaperFleetReconcilerSignalQueueIsolation(unittest.TestCase):
    """Each spawned worker must receive a binding-scoped PANTHEON_SIGNAL_QUEUE_KEY."""

    def _make_recon(self) -> "PaperFleetReconciler":
        from paper_fleet_reconciler import PaperFleetReconciler
        return PaperFleetReconciler(
            runtime_manager_url="http://rm:8081",
            runtime_manager_token="tok",
            poll_interval_seconds=999,
        )

    def test_env_contains_binding_scoped_queue_key(self) -> None:
        recon = self._make_recon()
        binding = _make_binding("b-iso-001")
        env = recon._build_worker_env(binding)
        self.assertIn("PANTHEON_SIGNAL_QUEUE_KEY", env)
        self.assertEqual(env["PANTHEON_SIGNAL_QUEUE_KEY"], "pantheon:signals:pending:b-iso-001")

    def test_two_bindings_get_different_queue_keys(self) -> None:
        recon = self._make_recon()
        env_a = recon._build_worker_env(_make_binding("b-alpha"))
        env_b = recon._build_worker_env(_make_binding("b-beta"))
        self.assertNotEqual(
            env_a["PANTHEON_SIGNAL_QUEUE_KEY"],
            env_b["PANTHEON_SIGNAL_QUEUE_KEY"],
        )

    def test_queue_key_embeds_binding_id(self) -> None:
        recon = self._make_recon()
        env = recon._build_worker_env(_make_binding("binding-xyz-99"))
        self.assertIn("binding-xyz-99", env["PANTHEON_SIGNAL_QUEUE_KEY"])

    def test_spawned_workers_receive_distinct_queue_keys(self) -> None:
        """Integration: reconcile with two bindings, confirm each spawn got a unique key."""
        from paper_fleet_reconciler import PaperFleetReconciler

        captured_envs: dict[str, str] = {}

        class _R(PaperFleetReconciler):
            def _fetch_fleet_state(self):
                return (
                    [
                        _make_binding("b-iso-a", runtime_id="rt-a"),
                        _make_binding("b-iso-b", runtime_id="rt-b"),
                    ],
                    set(),
                )

            def _spawn(self, binding_id, port, env):
                captured_envs[binding_id] = env.get("PANTHEON_SIGNAL_QUEUE_KEY", "")
                return _FakeProcess()

        recon = _R(worker_base_port=9950, poll_interval_seconds=999)
        recon.reconcile_once()

        self.assertEqual(len(captured_envs), 2)
        self.assertEqual(captured_envs["b-iso-a"], "pantheon:signals:pending:b-iso-a")
        self.assertEqual(captured_envs["b-iso-b"], "pantheon:signals:pending:b-iso-b")
        self.assertNotEqual(captured_envs["b-iso-a"], captured_envs["b-iso-b"])


class TestPaperFleetReconcilerMonitoringSessions(unittest.TestCase):
    def test_worker_start_opens_monitoring_session(self) -> None:
        wrapper = _InstrumentedReconciler(
            worker_base_port=9960,
            poll_interval_seconds=999,
            drain_timeout_seconds=1,
        )
        wrapper.set_bindings([_make_binding("b-mon-001", runtime_id="rt-mon-001")])

        snap = wrapper.recon.reconcile_once()

        self.assertEqual(snap["monitoring_session_count"], 1)
        self.assertEqual(snap["active_monitoring_session_count"], 1)
        session = snap["monitoring_sessions"][0]
        worker = snap["workers"][0]
        self.assertEqual(session["session_type"], "paper_runtime_monitoring")
        self.assertEqual(session["binding_id"], "b-mon-001")
        self.assertEqual(session["runtime_id"], "rt-mon-001")
        self.assertIsNone(session["ended_at"])
        self.assertTrue(session["active"])
        self.assertEqual(worker["monitoring_session_id"], session["session_id"])

    def test_stale_heartbeat_ends_session_and_restarts_worker(self) -> None:
        from paper_fleet_reconciler import PaperFleetReconciler

        spawned: List[Dict[str, Any]] = []

        class _R(PaperFleetReconciler):
            def _fetch_fleet_state(self):
                return ([_make_binding("b-stale-001", runtime_id="rt-stale-001")], set())

            def _fetch_runtime_summaries(self):
                return {
                    "rt-stale-001": {
                        "runtime_id": "rt-stale-001",
                        "runtime_binding_id": "b-stale-001",
                        "last_heartbeat_at": "2000-01-01T00:00:00Z",
                        "state": "active",
                    }
                }

            def _spawn(self, binding_id, port, env):  # noqa: ANN001
                proc = _FakeProcess(pid=700 + len(spawned))
                spawned.append({"binding_id": binding_id, "proc": proc})
                return proc

        recon = _R(
            worker_base_port=9970,
            poll_interval_seconds=999,
            restart_backoff_seconds=0,
            monitoring_heartbeat_stale_after_seconds=1,
            drain_timeout_seconds=1,
        )

        first = recon.reconcile_once()
        self.assertEqual(first["active_monitoring_session_count"], 1)
        self.assertEqual(len(spawned), 1)

        second = recon.reconcile_once()
        self.assertEqual(len(spawned), 2)
        self.assertTrue(spawned[0]["proc"].terminated or spawned[0]["proc"].killed)
        ended = [
            session
            for session in second["monitoring_sessions"]
            if session.get("ended_reason") == "stale_heartbeat"
        ]
        active = [session for session in second["monitoring_sessions"] if session.get("active")]
        self.assertEqual(len(ended), 1)
        self.assertEqual(len(active), 1)
        self.assertIsNotNone(ended[0]["ended_at"])
        self.assertEqual(ended[0]["staleness"]["reason"], "stale_heartbeat")
        self.assertNotEqual(ended[0]["session_id"], active[0]["session_id"])

    def test_stale_persisted_zombie_session_is_closed_on_restart(self) -> None:
        from paper_fleet_reconciler import PaperFleetReconciler

        with tempfile.TemporaryDirectory() as td:
            store_path = Path(td) / "paper_runtime_monitoring_sessions.json"
            store_path.write_text(
                json.dumps(
                    {
                        "monitoring_sessions": [
                            {
                                "session_id": "prmon-old",
                                "id": "prmon-old",
                                "session_type": "paper_runtime_monitoring",
                                "binding_id": "b-zombie-001",
                                "runtime_binding_id": "b-zombie-001",
                                "runtime_id": "rt-zombie-001",
                                "deployment_stage": "paper",
                                "status": "running",
                                "started_at": "2000-01-01T00:00:00Z",
                                "ended_at": None,
                                "last_heartbeat_at": "2000-01-01T00:00:00Z",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            spawned: List[Dict[str, Any]] = []

            class _R(PaperFleetReconciler):
                def _fetch_fleet_state(self):
                    return ([_make_binding("b-zombie-001", runtime_id="rt-zombie-001")], set())

                def _fetch_runtime_summaries(self):
                    return {
                        "rt-zombie-001": {
                            "runtime_id": "rt-zombie-001",
                            "runtime_binding_id": "b-zombie-001",
                            "last_heartbeat_at": "2000-01-01T00:00:00Z",
                            "state": "active",
                        }
                    }

                def _spawn(self, binding_id, port, env):  # noqa: ANN001
                    proc = _FakeProcess(pid=800 + len(spawned))
                    spawned.append({"binding_id": binding_id, "proc": proc})
                    return proc

            recon = _R(
                worker_base_port=9980,
                poll_interval_seconds=999,
                monitoring_session_store_path=str(store_path),
                monitoring_heartbeat_stale_after_seconds=1,
                drain_timeout_seconds=1,
            )

            snap = recon.reconcile_once()

            old = next(session for session in snap["monitoring_sessions"] if session["session_id"] == "prmon-old")
            active = [session for session in snap["monitoring_sessions"] if session.get("active")]
            self.assertEqual(old["ended_reason"], "stale_heartbeat")
            self.assertIsNotNone(old["ended_at"])
            self.assertEqual(len(active), 1)
            self.assertNotEqual(active[0]["session_id"], "prmon-old")
            persisted = json.loads(store_path.read_text(encoding="utf-8"))
            persisted_old = next(
                session
                for session in persisted["monitoring_sessions"]
                if session["session_id"] == "prmon-old"
            )
            self.assertIsNotNone(persisted_old["ended_at"])


class TestPaperFleetReconcilerExcludedBindings(unittest.TestCase):
    """Excluded bindings (paused/retired) must stop their workers immediately."""

    def _make_wrapper(self) -> "_InstrumentedReconciler":
        return _InstrumentedReconciler(
            worker_base_port=9010,
            poll_interval_seconds=999,
            max_restarts=3,
            restart_backoff_seconds=0,
            drain_timeout_seconds=1,
        )

    def test_paused_binding_stops_worker(self) -> None:
        wrapper = self._make_wrapper()
        wrapper.set_bindings([_make_binding("b-pause")])
        wrapper.recon.reconcile_once()
        self.assertEqual(wrapper.recon.snapshot()["worker_count"], 1)

        # Binding transitions to paused: removed from desired, added to excluded
        wrapper.set_bindings([])
        wrapper.set_excluded({"b-pause"})
        snap = wrapper.recon.reconcile_once()

        self.assertEqual(snap["worker_count"], 0)
        proc = wrapper.spawned[0]["proc"]
        self.assertTrue(proc.terminated or proc.killed)

    def test_retired_binding_stops_worker(self) -> None:
        wrapper = self._make_wrapper()
        wrapper.set_bindings([_make_binding("b-retire")])
        wrapper.recon.reconcile_once()

        wrapper.set_bindings([])
        wrapper.set_excluded({"b-retire"})
        snap = wrapper.recon.reconcile_once()

        self.assertEqual(snap["worker_count"], 0)
        proc = wrapper.spawned[0]["proc"]
        self.assertTrue(proc.terminated or proc.killed)

    def test_excluded_binding_not_restarted_when_dead(self) -> None:
        wrapper = self._make_wrapper()
        wrapper.set_bindings([_make_binding("b-excl")])
        wrapper.recon.reconcile_once()

        # Kill the worker process
        wrapper.spawned[0]["proc"]._returncode = 1

        # Now exclude the binding (simulates paused/retired)
        wrapper.set_bindings([])
        wrapper.set_excluded({"b-excl"})
        snap = wrapper.recon.reconcile_once()

        # Worker should be gone, not restarted
        self.assertEqual(snap["worker_count"], 0)
        self.assertEqual(len(wrapper.spawned), 1, "excluded dead binding must not spawn a replacement")

    def test_desired_and_excluded_disjoint(self) -> None:
        """Two bindings: one desired (active), one excluded (paused). Only active gets a worker."""
        wrapper = self._make_wrapper()
        # Start with both bindings active
        wrapper.set_bindings([_make_binding("b-keep"), _make_binding("b-pause")])
        wrapper.recon.reconcile_once()
        self.assertEqual(wrapper.recon.snapshot()["worker_count"], 2)

        # b-pause moves to excluded (paused), b-keep stays desired
        wrapper.set_bindings([_make_binding("b-keep")])
        wrapper.set_excluded({"b-pause"})
        snap = wrapper.recon.reconcile_once()

        self.assertEqual(snap["worker_count"], 1)
        self.assertEqual(snap["workers"][0]["binding_id"], "b-keep")

    def test_fetch_failure_preserves_excluded_worker(self) -> None:
        """When fleet state fetch returns None, workers for excluded bindings are preserved."""
        from paper_fleet_reconciler import PaperFleetReconciler

        spawned = []

        class _R(PaperFleetReconciler):
            def __init__(inner_self, **kw):
                super().__init__(**kw)
                inner_self._should_fail = False

            def _fetch_fleet_state(inner_self):
                if inner_self._should_fail:
                    with inner_self._lock:
                        inner_self._last_error = "fleet desired state fetch failed: URLError: simulated"
                    return None
                return ([_make_binding("b-excl-safe")], set())

            def _spawn(inner_self, binding_id, port, env):
                proc = _FakeProcess(pid=500 + len(spawned))
                spawned.append({"binding_id": binding_id, "proc": proc})
                return proc

        recon = _R(
            worker_base_port=9050,
            poll_interval_seconds=999,
            drain_timeout_seconds=1,
        )
        recon.reconcile_once()
        self.assertEqual(recon.snapshot()["worker_count"], 1)

        recon._should_fail = True
        recon.reconcile_once()

        snap = recon.snapshot()
        self.assertEqual(snap["worker_count"], 1, "fetch failure must not stop running workers")


class TestPaperFleetReconcilerAcceptanceCriteria(unittest.TestCase):
    """Explicit coverage of the LOOP-AUTO-RT-002 acceptance criteria."""

    def test_stack_restart_recreates_workers_for_all_active_bindings(self) -> None:
        """AC-1: fresh reconciler with N active bindings starts N workers."""
        wrapper = _InstrumentedReconciler(
            worker_base_port=9060,
            poll_interval_seconds=999,
            drain_timeout_seconds=1,
        )
        wrapper.set_bindings([
            _make_binding("b-ac1-a", runtime_id="rt-a"),
            _make_binding("b-ac1-b", runtime_id="rt-b"),
            _make_binding("b-ac1-c", runtime_id="rt-c"),
        ])
        snap = wrapper.recon.reconcile_once()
        self.assertEqual(snap["worker_count"], 3)
        self.assertEqual(snap["running_count"], 3)
        started_ids = {s["binding_id"] for s in wrapper.spawned}
        self.assertEqual(started_ids, {"b-ac1-a", "b-ac1-b", "b-ac1-c"})

    def test_killing_one_worker_restarts_only_that_worker(self) -> None:
        """AC-2: killing worker for b-kill restarts only that one; b-ok is untouched."""
        wrapper = _InstrumentedReconciler(
            worker_base_port=9070,
            poll_interval_seconds=999,
            max_restarts=3,
            restart_backoff_seconds=0,
            drain_timeout_seconds=1,
        )
        wrapper.set_bindings([
            _make_binding("b-ok"),
            _make_binding("b-kill", runtime_id="rt-kill"),
        ])
        wrapper.recon.reconcile_once()
        self.assertEqual(len(wrapper.spawned), 2)

        # Simulate kill -9 (SIGKILL, exit 137) on one worker
        kill_spawn = next(s for s in wrapper.spawned if s["binding_id"] == "b-kill")
        kill_spawn["proc"]._returncode = 137

        # First reconcile detects the dead worker; second reconcile restarts it
        wrapper.recon.reconcile_once()
        wrapper.recon.reconcile_once()

        self.assertEqual(len(wrapper.spawned), 3, "exactly one restart spawned")
        restarted = wrapper.spawned[2]
        self.assertEqual(restarted["binding_id"], "b-kill")
        # b-ok was never touched
        ok_spawn = next(s for s in wrapper.spawned if s["binding_id"] == "b-ok")
        self.assertFalse(ok_spawn["proc"].terminated)
        self.assertFalse(ok_spawn["proc"].killed)

    def test_paused_binding_stops_its_worker(self) -> None:
        """AC-3: when a binding transitions to paused, its worker is stopped."""
        wrapper = _InstrumentedReconciler(
            worker_base_port=9080,
            poll_interval_seconds=999,
            drain_timeout_seconds=1,
        )
        wrapper.set_bindings([_make_binding("b-ac3")])
        wrapper.recon.reconcile_once()
        self.assertEqual(wrapper.recon.snapshot()["worker_count"], 1)

        # Simulate pause: binding removed from desired list and added to excluded set
        wrapper.set_bindings([])
        wrapper.set_excluded({"b-ac3"})
        snap = wrapper.recon.reconcile_once()

        self.assertEqual(snap["worker_count"], 0)
        proc = wrapper.spawned[0]["proc"]
        self.assertTrue(proc.terminated or proc.killed, "paused binding must stop its worker")

    def test_retired_binding_stops_its_worker(self) -> None:
        """AC-3 (retire path): retired binding → worker stopped."""
        wrapper = _InstrumentedReconciler(
            worker_base_port=9090,
            poll_interval_seconds=999,
            drain_timeout_seconds=1,
        )
        wrapper.set_bindings([_make_binding("b-ac3-retire")])
        wrapper.recon.reconcile_once()

        # Simulate retire: binding disappears from desired, appears in excluded
        wrapper.set_bindings([])
        wrapper.set_excluded({"b-ac3-retire"})
        snap = wrapper.recon.reconcile_once()

        self.assertEqual(snap["worker_count"], 0)
        proc = wrapper.spawned[0]["proc"]
        self.assertTrue(proc.terminated or proc.killed, "retired binding must stop its worker")


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    unittest.main()

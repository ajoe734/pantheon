"""Tests for PaperFleetReconciler."""
from __future__ import annotations

import json
import multiprocessing
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE_DIR = Path(__file__).resolve().parent
for _p in (REPO_ROOT, SERVICE_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def _unit_leader_store():
    from services.paper_fleet_reconciler.paper_fleet_reconciler import InMemoryFencedLeaderStore

    return InMemoryFencedLeaderStore()


def _redis_lease_process(
    redis_url: str,
    reconciler_id: str,
    ttl_seconds: float,
    result_queue,
) -> None:
    import redis
    from paper_fleet_reconciler import (
        PaperFleetReconciler,
        RedisFencedLeaderStore,
    )

    store = RedisFencedLeaderStore(
        redis.Redis.from_url(redis_url, decode_responses=True),
        lease_key="pantheon:test:l12-cap:leader",
    )
    reconciler = PaperFleetReconciler(
        leader_store=store,
        leader_lease_ttl_seconds=ttl_seconds,
        reconciler_id=reconciler_id,
    )
    acquired = reconciler.try_acquire_lease()
    result_queue.put(
        {
            "reconciler_id": reconciler_id,
            "acquired": acquired,
            "token": reconciler._fence_token,
            "expires_at_ms": reconciler._lease_expires_at_ms,
        }
    )


class _RealRedisDockerTestCase(unittest.TestCase):
    redis_url = ""
    _container_name = ""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        if shutil.which("docker") is None:
            raise unittest.SkipTest("docker is required for real Redis proof")
        if subprocess.run(
            ["docker", "info"],
            check=False,
            capture_output=True,
            text=True,
        ).returncode != 0:
            raise unittest.SkipTest("docker daemon is unavailable for real Redis proof")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        cls._container_name = f"l12-cap-leader-{uuid.uuid4().hex[:10]}"
        started = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-d",
                "--name",
                cls._container_name,
                "-p",
                f"127.0.0.1:{port}:6379",
                "redis:7-alpine",
                "redis-server",
                "--save",
                "",
                "--appendonly",
                "no",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if started.returncode != 0:
            raise unittest.SkipTest(f"could not start Redis container: {started.stderr}")
        cls.redis_url = f"redis://127.0.0.1:{port}/14"
        import redis

        client = redis.Redis.from_url(cls.redis_url, decode_responses=True)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                if client.ping():
                    return
            except Exception:
                time.sleep(0.05)
        cls.tearDownClass()
        raise RuntimeError("real Redis container did not become ready")

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._container_name:
            subprocess.run(
                ["docker", "rm", "-f", cls._container_name],
                check=False,
                capture_output=True,
                text=True,
            )
            cls._container_name = ""
        super().tearDownClass()

    def setUp(self) -> None:
        import redis

        self.redis = redis.Redis.from_url(self.redis_url, decode_responses=True)
        self.redis.flushdb()


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
                from paper_fleet_reconciler import validate_executable_binding
                valid = []
                excluded = set(self._fake_excluded_ids)
                for b in self._fake_bindings:
                    if isinstance(b, dict):
                        is_val, _ = validate_executable_binding(b)
                        if is_val:
                            valid.append(b)
                        elif b.get("binding_id"):
                            excluded.add(str(b["binding_id"]))
                return (valid, excluded)

            def _spawn(inner_self, binding_id: str, port: int, env: Any) -> _FakeProcess:
                pid = self._next_pid
                self._next_pid += 1
                proc = _FakeProcess(pid=pid)
                self.spawned.append({"binding_id": binding_id, "port": port, "pid": pid, "proc": proc})
                return proc

        kwargs.setdefault("leader_store", _unit_leader_store())
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

    def test_invalid_binding_no_spawn(self) -> None:
        invalid_b = _make_binding("b-invalid", runtime_id="")
        self.wrapper.set_bindings([invalid_b])
        snap = self.recon.reconcile_once()
        self.assertEqual(snap["worker_count"], 0)
        self.assertEqual(len(self.wrapper.spawned), 0)

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

    def test_worker_spawn_does_not_use_undrained_stdio_pipes(self) -> None:
        from paper_fleet_reconciler import PaperFleetReconciler

        recon = PaperFleetReconciler(
            runtime_manager_url="http://rm:8081",
            runtime_manager_token="tok",
            poll_interval_seconds=999,
            worker_script_path="/tmp/paper_runtime.py",
        )

        with patch("paper_fleet_reconciler.subprocess.Popen", return_value=_FakeProcess()) as popen:
            recon._spawn("b-stdio-001", 8123, {"PATH": "/usr/bin", "PORT": "8123"})

        _args, kwargs = popen.call_args
        self.assertNotIn("stdout", kwargs)
        self.assertNotIn("stderr", kwargs)
        self.assertTrue(kwargs["close_fds"])


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
            metadata={"persona_id": "persona-x"},
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
        self.assertEqual(env["PANTHEON_PERSONA_ID"], "persona-x")
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

    def test_worker_env_contains_performance_mark_contract(self) -> None:
        from paper_fleet_reconciler import PaperFleetReconciler

        recon = PaperFleetReconciler(
            source_ingest_url="http://source-ingest:8097/",
            performance_mark_max_age_seconds=172800,
            performance_state_root="/data/runtime/paper-performance",
            poll_interval_seconds=999,
        )
        env = recon._build_worker_env(_make_binding("binding-safe-001"))

        self.assertEqual(
            env["PANTHEON_SOURCE_INGEST_URL"],
            "http://source-ingest:8097",
        )
        self.assertEqual(
            env["PANTHEON_PERFORMANCE_MARK_MAX_AGE_SECONDS"],
            "172800",
        )
        state_path = Path(env["PANTHEON_PERFORMANCE_STATE_PATH"])
        self.assertEqual(state_path.parent, Path("/data/runtime/paper-performance"))
        self.assertTrue(state_path.name.startswith("binding-safe-001-"))
        self.assertEqual(state_path.suffix, ".json")

    def test_binding_id_cannot_escape_performance_state_root(self) -> None:
        from paper_fleet_reconciler import PaperFleetReconciler

        state_root = Path("/data/runtime/paper-performance")
        recon = PaperFleetReconciler(
            performance_state_root=str(state_root),
            poll_interval_seconds=999,
        )
        hostile_ids = ("../../shared/binding", "..\\..\\shared\\binding")
        state_paths = {
            Path(
                recon._build_worker_env(_make_binding(binding_id))[
                    "PANTHEON_PERFORMANCE_STATE_PATH"
                ]
            )
            for binding_id in hostile_ids
        }

        self.assertEqual(len(state_paths), len(hostile_ids))
        for state_path in state_paths:
            self.assertEqual(state_path.parent, state_root)
            self.assertNotIn("..", state_path.name)


class TestPaperPerformanceComposeWiring(unittest.TestCase):
    def test_fleet_and_static_runtimes_use_canonical_mark_source_and_state_volume(self) -> None:
        import yaml

        compose_path = REPO_ROOT / "docker-compose.yml"
        services = yaml.safe_load(compose_path.read_text(encoding="utf-8"))["services"]

        fleet = services["paper-fleet-reconciler"]
        fleet_env = fleet["environment"]
        self.assertEqual(
            fleet_env["PANTHEON_SOURCE_INGEST_URL"],
            "http://source-ingest:8097",
        )
        self.assertEqual(
            fleet_env["PANTHEON_PERFORMANCE_MARK_MAX_AGE_SECONDS"],
            "${PANTHEON_PERFORMANCE_MARK_MAX_AGE_SECONDS:-172800}",
        )
        self.assertEqual(
            fleet_env["PANTHEON_PERFORMANCE_STATE_ROOT"],
            "/data/runtime/paper-performance",
        )
        self.assertIn("runtime-data:/data/runtime", fleet["volumes"])
        self.assertEqual(
            fleet["depends_on"]["source-ingest"]["condition"],
            "service_healthy",
        )

        static_runtime = services["pantheon-paper-runtime"]
        static_env = static_runtime["environment"]
        self.assertEqual(static_runtime["profiles"], ["static-paper-runtime"])
        self.assertNotIn("profiles", fleet)
        self.assertEqual(
            static_env["PANTHEON_SOURCE_INGEST_URL"],
            "http://source-ingest:8097",
        )
        self.assertEqual(
            static_env["PANTHEON_PERFORMANCE_MARK_MAX_AGE_SECONDS"],
            "${PANTHEON_PERFORMANCE_MARK_MAX_AGE_SECONDS:-172800}",
        )
        self.assertEqual(
            static_env["PANTHEON_PERFORMANCE_STATE_PATH"],
            "/data/runtime/paper-performance/static-paper-runtime.json",
        )
        self.assertEqual(
            static_env["PANTHEON_PAPER_SYNTHETIC_MARKET_DATA"],
            "${PANTHEON_PAPER_SYNTHETIC_MARKET_DATA:-false}",
        )
        self.assertIn("runtime-data:/data/runtime", static_runtime["volumes"])
        self.assertEqual(
            static_runtime["depends_on"]["source-ingest"]["condition"],
            "service_healthy",
        )


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

        recon = _TestFetch(
            worker_base_port=9750,
            poll_interval_seconds=999,
            leader_store=_unit_leader_store(),
        )
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
            leader_store=_unit_leader_store(),
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

        recon = _R(
            worker_base_port=9950,
            poll_interval_seconds=999,
            leader_store=_unit_leader_store(),
        )
        recon.reconcile_once()

        self.assertEqual(len(captured_envs), 2)
        self.assertEqual(captured_envs["b-iso-a"], "pantheon:signals:pending:b-iso-a")
        self.assertEqual(captured_envs["b-iso-b"], "pantheon:signals:pending:b-iso-b")
        self.assertNotEqual(captured_envs["b-iso-a"], captured_envs["b-iso-b"])


class TestPaperFleetMinimumFunctionalClosure(unittest.TestCase):
    """One active paper binding must reach a durable paper-runtime readback."""

    def test_binding_starts_worker_and_duplicate_replay_keeps_one_fill(self) -> None:
        """L12-MIN-CAP: fleet → fill/position → telemetry stays paper-only."""
        from services.execution.lean_runtime.paper_runtime import PaperRuntimeService
        from services.execution.lean_runtime.pending_signal_store import (
            InMemoryPendingSignalStore,
        )
        from services.execution.lean_runtime.runtime_identity import RuntimeIdentity
        from services.execution.lean_runtime.signal_producer import (
            build_decision_signals,
        )
        from services.trade_journey.correlation_envelope import propagate_envelope
        from paper_fleet_reconciler import PaperFleetReconciler

        binding = _make_binding(
            "binding-l12-min-cap",
            runtime_id="runtime-l12-min-cap",
            capital_pool_id="pool-l12-paper",
            plan_id="plan-l12-min-dep",
            metadata={"persona_id": "persona-l12-paper"},
        )
        [signal] = build_decision_signals(
            {
                "decision_id": "decision-l12-min-cap",
                "signal_id": "signal-l12-min-cap",
                "strategy_id": "strategy-l12-paper",
                "timestamp": datetime.now(timezone.utc)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z"),
                "tenant_id": "tenant-l12",
                "environment": "paper",
                "symbol": "AAPL.US",
                "action": "BUY",
                "direction": "LONG",
                "quantity": 2,
                "quantity_type": "SHARES",
                "run_id": "run-l12-min-cap",
                "metadata": {"capital_pool_id": binding["capital_pool_id"]},
            },
            binding_id=binding["binding_id"],
            runtime_id=binding["runtime_id"],
        )
        store = InMemoryPendingSignalStore([signal])

        class _RuntimeManagerClient:
            def list_all(self):
                return [dict(binding)]

        class _TelemetryCapture:
            enabled = True

            def __init__(self) -> None:
                self.events: List[Dict[str, Any]] = []

            def build_event(
                self,
                event_type: str,
                metrics: Dict[str, Any],
                metadata: Optional[Dict[str, Any]] = None,
                *,
                event_id: Optional[str] = None,
                created_at: Optional[str] = None,
            ) -> Dict[str, Any]:
                event_metadata = dict(metadata or {})
                envelope = event_metadata.get("correlation_envelope")
                sequence_no = event_metadata.get("sequence_no")
                causal_parent_id = event_metadata.get("causal_parent_id")
                payload: Dict[str, Any] = {
                    "event_id": event_id,
                    "event_type": event_type,
                    "created_at": created_at,
                    "metrics": dict(metrics),
                    "metadata": event_metadata,
                }
                if (
                    isinstance(envelope, dict)
                    and isinstance(sequence_no, int)
                    and event_id
                    and created_at
                    and causal_parent_id
                ):
                    outgoing_envelope = propagate_envelope(
                        envelope,
                        producer="execution.paper_runtime",
                        event_id=event_id,
                        event_time=created_at,
                    )
                    payload.update(
                        {
                            "aggregate_type": "trade_journey",
                            "aggregate_id": outgoing_envelope["journey_id"],
                            "sequence_no": sequence_no,
                            "causal_parent_id": causal_parent_id,
                            "correlation_envelope": outgoing_envelope,
                        }
                    )
                return payload

            def emit_payload(self, payload: Dict[str, Any]) -> bool:
                self.events.append(json.loads(json.dumps(dict(payload))))
                return True

            def emit(
                self,
                event_type: str,
                metrics: Dict[str, Any],
                metadata: Optional[Dict[str, Any]] = None,
            ) -> bool:
                self.events.append(
                    {
                        "event_type": event_type,
                        "metrics": dict(metrics),
                        "metadata": dict(metadata or {}),
                    }
                )
                return True

            def emit_heartbeat(self, metadata: Optional[Dict[str, Any]] = None) -> bool:
                return self.emit("heartbeat", {"heartbeat": 1}, metadata)

            def snapshot(self) -> Dict[str, Any]:
                return {
                    "enabled": True,
                    "url": "memory://l12-telemetry",
                    "sent": len(self.events),
                    "failed": 0,
                    "last_error": None,
                }

        telemetry = _TelemetryCapture()
        captured_envs: List[Dict[str, str]] = []
        runtime_snapshots: List[Dict[str, Any]] = []

        with tempfile.TemporaryDirectory() as tempdir:
            state_root = Path(tempdir) / "paper-performance"
            lifecycle_path = Path(tempdir) / "lifecycle-outbox.json"
            test_case = self

            class _RuntimeWorkerReconciler(PaperFleetReconciler):
                def _fetch_fleet_state(self):
                    return ([dict(binding)], set())

                def _spawn(self, binding_id, port, env):  # noqa: ANN001
                    test_case.assertEqual(binding_id, binding["binding_id"])
                    worker_env = dict(env)
                    captured_envs.append(worker_env)
                    with patch.dict(os.environ, worker_env, clear=False):
                        service = PaperRuntimeService(
                            store=store,
                            identity=RuntimeIdentity.from_env(worker_env),
                            runtime_manager_client=_RuntimeManagerClient(),
                            telemetry_emitter=telemetry,
                            lifecycle_outbox_path=lifecycle_path,
                            poll_interval_seconds=3600,
                        )
                        service.drain_once()
                        # A single fully delivered run is flushed immediately;
                        # this is the normal rebalance completion boundary.
                        service._consumer.flush_rebalance(
                            signal["run_id"], service._algo
                        )
                        store.enqueue(dict(signal))
                        runtime_snapshots.append(service.drain_once())
                    return _FakeProcess(pid=1701)

            reconciler = _RuntimeWorkerReconciler(
                runtime_manager_url="http://runtime-manager.test",
                runtime_manager_token="runtime-control-token",
                worker_base_port=9130,
                poll_interval_seconds=999,
                performance_state_root=str(state_root),
                leader_store=_unit_leader_store(),
                extra_env={
                    "PANTHEON_RUNTIME_ROLE": "pantheon-paper-execution-runtime",
                    "PANTHEON_WORKSPACE_REF": "workspace-l12-paper",
                    "PANTHEON_AUTH_PROFILE_REF": "auth-profile-l12-paper",
                    "PANTHEON_SESSION_ID": "session-l12-paper",
                    "PANTHEON_TRACE_ID": str(uuid.uuid4()),
                    "PANTHEON_REQUEST_ID": "request-l12-paper",
                },
            )
            fleet_snapshot = reconciler.reconcile_once()

            self.assertEqual(fleet_snapshot["worker_count"], 1)
            self.assertEqual(fleet_snapshot["running_count"], 1)
            self.assertEqual(len(captured_envs), 1)
            worker_env = captured_envs[0]
            self.assertEqual(
                worker_env["PANTHEON_SIGNAL_QUEUE_KEY"],
                "pantheon:signals:pending:binding-l12-min-cap",
            )
            self.assertEqual(
                worker_env["PANTHEON_RUNTIME_BINDING_ID"], binding["binding_id"]
            )

            self.assertEqual(len(runtime_snapshots), 1)
            runtime_snapshot = runtime_snapshots[0]
            self.assertEqual(runtime_snapshot["status"], "ok")
            positions = runtime_snapshot["paper_state"]["positions"]
            self.assertEqual(len(positions), 1)
            self.assertEqual(positions[0]["quantity"], 2.0)
            self.assertEqual(runtime_snapshot["paper_state"]["execution_event_count"], 2)
            recent_event_types = [
                event["event_type"]
                for event in runtime_snapshot["paper_state"]["recent_order_events"]
            ]
            self.assertEqual(
                recent_event_types,
                ["paper_fill_simulated", "paper_order_simulated"],
            )
            duplicate_event = runtime_snapshot["paper_state"]["recent_order_events"][-1]
            self.assertEqual(
                duplicate_event["metadata"]["noop_reason"], "duplicate_signal_id"
            )
            self.assertEqual(
                duplicate_event["metadata"]["duplicate_signal_id"],
                signal["signal_id"],
            )

            ledger_path = Path(worker_env["PANTHEON_PERFORMANCE_STATE_PATH"])
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            self.assertEqual(ledger["binding_id"], binding["binding_id"])
            self.assertEqual(ledger["fill_count"], 1)
            self.assertEqual(ledger["holdings"]["AAPL"], 2.0)

        telemetry_types = [event["event_type"] for event in telemetry.events]
        self.assertIn("paper_fill_simulated", telemetry_types)
        self.assertIn("position_snapshot", telemetry_types)
        fill_event = next(
            event for event in telemetry.events
            if event["event_type"] == "paper_fill_simulated"
        )
        self.assertFalse(fill_event["metadata"]["is_real_order"])
        self.assertFalse(fill_event["metadata"]["is_real_capital"])


class TestPaperFleetReconcilerMonitoringSessions(unittest.TestCase):
    def test_runtime_summary_request_uses_telemetry_service_token(self) -> None:
        from paper_fleet_reconciler import PaperFleetReconciler

        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(
            {
                "summaries": [
                    {
                        "runtime_id": "rt-authenticated",
                        "state": "active",
                    }
                ]
            }
        ).encode("utf-8")

        recon = PaperFleetReconciler(
            telemetry_api_url="http://telemetry.test",
            telemetry_service_token="telemetry-service-secret",
            telemetry_tenant_id="tenant-paper",
            leader_store=_unit_leader_store(),
        )
        with patch("urllib.request.urlopen", return_value=response) as urlopen:
            summaries = recon._fetch_runtime_summaries()

        self.assertIn("rt-authenticated", summaries or {})
        request = urlopen.call_args.args[0]
        self.assertEqual(
            request.get_header("Authorization"),
            "Bearer telemetry-service-secret",
        )
        self.assertEqual(request.get_header("X-tenant-id"), "tenant-paper")
        self.assertIsNone(recon.snapshot()["monitoring_last_error"])

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
            leader_store=_unit_leader_store(),
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
                leader_store=_unit_leader_store(),
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

    def test_staleness_marker_does_not_count_as_live_session_on_restart(self) -> None:
        from paper_fleet_reconciler import PaperFleetReconciler

        with tempfile.TemporaryDirectory() as td:
            store_path = Path(td) / "paper_runtime_monitoring_sessions.json"
            store_path.write_text(
                json.dumps(
                    {
                        "monitoring_sessions": [
                            {
                                "session_id": "prmon-stale-marker",
                                "id": "prmon-stale-marker",
                                "session_type": "paper_runtime_monitoring",
                                "binding_id": "b-marker-001",
                                "runtime_binding_id": "b-marker-001",
                                "runtime_id": "rt-marker-001",
                                "deployment_stage": "paper",
                                "status": "running",
                                "active": True,
                                "started_at": "2026-06-09T00:00:00Z",
                                "ended_at": None,
                                "last_heartbeat_at": "2026-06-09T00:00:00Z",
                                "staleness": {
                                    "status": "stale",
                                    "reason": "stale_heartbeat",
                                    "last_known_at": "2026-06-09T00:00:00Z",
                                    "age_seconds": 600,
                                    "threshold_seconds": 90,
                                },
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            spawned: List[Dict[str, Any]] = []

            class _R(PaperFleetReconciler):
                def _fetch_fleet_state(self):
                    return ([_make_binding("b-marker-001", runtime_id="rt-marker-001")], set())

                def _fetch_runtime_summaries(self):
                    return None

                def _spawn(self, binding_id, port, env):  # noqa: ANN001
                    proc = _FakeProcess(pid=900 + len(spawned))
                    spawned.append({"binding_id": binding_id, "proc": proc})
                    return proc

            recon = _R(
                worker_base_port=9990,
                poll_interval_seconds=999,
                monitoring_session_store_path=str(store_path),
                monitoring_heartbeat_stale_after_seconds=90,
                drain_timeout_seconds=1,
                leader_store=_unit_leader_store(),
            )

            snap = recon.reconcile_once()

            old = next(
                session
                for session in snap["monitoring_sessions"]
                if session["session_id"] == "prmon-stale-marker"
            )
            active = [session for session in snap["monitoring_sessions"] if session.get("active")]
            self.assertEqual(len(spawned), 1)
            self.assertFalse(old["active"])
            self.assertEqual(old["ended_reason"], "stale_heartbeat")
            self.assertEqual(old["terminal_reason"], "stale_heartbeat")
            self.assertIsNotNone(old["ended_at"])
            self.assertEqual(len(active), 1)
            self.assertNotEqual(active[0]["session_id"], "prmon-stale-marker")


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
            leader_store=_unit_leader_store(),
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


class TestLeaderLease(unittest.TestCase):

    def test_two_reconcilers_leader_lease_convergence(self) -> None:
        """Requirement (d): Two concurrent reconcilers converge to a single leader owner."""
        shared_store = _unit_leader_store()
        w1 = _InstrumentedReconciler(
            worker_base_port=9100,
            leader_store=shared_store,
        )
        w2 = _InstrumentedReconciler(
            worker_base_port=9200,
            leader_store=shared_store,
        )

        binding = _make_binding("b-lease")
        w1.set_bindings([binding])
        w2.set_bindings([binding])

        # w1 runs first and acquires lease
        s1 = w1.recon.reconcile_once()
        self.assertTrue(w1.recon.is_leader)
        self.assertEqual(len(w1.spawned), 1)
        self.assertGreater(s1["fence_token"], 0)

        # w2 runs and yields to w1
        s2 = w2.recon.reconcile_once()
        self.assertFalse(w2.recon.is_leader)
        self.assertEqual(len(w2.spawned), 0)
        self.assertIsNone(s2["fence_token"])

    def test_missing_leader_store_fails_closed(self) -> None:
        from paper_fleet_reconciler import PaperFleetReconciler

        reconciler = PaperFleetReconciler()
        self.assertFalse(reconciler.try_acquire_lease())
        self.assertFalse(reconciler.is_leader)
        self.assertIn("not configured", reconciler.snapshot()["last_error"])

    def test_blocked_spawn_expiry_terminates_stale_leader_child(self) -> None:
        from paper_fleet_reconciler import (
            InMemoryFencedLeaderStore,
            PaperFleetReconciler,
        )

        shared_store = InMemoryFencedLeaderStore()
        spawn_entered = threading.Event()
        release_spawn = threading.Event()
        stale_process = _FakeProcess(pid=1201)
        successor_process = _FakeProcess(pid=1202)

        class _BlockedReconciler(PaperFleetReconciler):
            def _fetch_fleet_state(self):
                return ([_make_binding("b-blocked-spawn")], set())

            def _spawn(self, binding_id, port, env):  # noqa: ANN001
                spawn_entered.set()
                release_spawn.wait(timeout=5)
                return stale_process

        class _SuccessorReconciler(PaperFleetReconciler):
            def _fetch_fleet_state(self):
                return ([_make_binding("b-blocked-spawn")], set())

            def _spawn(self, binding_id, port, env):  # noqa: ANN001
                return successor_process

        stale = _BlockedReconciler(
            leader_store=shared_store,
            leader_lease_ttl_seconds=0.1,
            reconciler_id="reconciler-stale",
            drain_timeout_seconds=0.1,
        )
        successor = _SuccessorReconciler(
            leader_store=shared_store,
            leader_lease_ttl_seconds=0.1,
            reconciler_id="reconciler-successor",
            drain_timeout_seconds=0.1,
        )
        stale_result: dict[str, Any] = {}

        def _run_stale() -> None:
            stale_result["snapshot"] = stale.reconcile_once()

        stale_thread = threading.Thread(target=_run_stale)
        stale_thread.start()
        self.assertTrue(spawn_entered.wait(timeout=2))
        stale_token = stale._fence_token

        time.sleep(0.15)
        successor_snapshot = successor.reconcile_once()
        self.assertGreater(successor._fence_token, stale_token)
        self.assertEqual(successor_snapshot["worker_count"], 1)

        release_spawn.set()
        stale_thread.join(timeout=2)
        self.assertFalse(stale_thread.is_alive())
        self.assertFalse(stale.is_leader)
        self.assertEqual(stale_result["snapshot"]["worker_count"], 0)
        self.assertTrue(stale_process.terminated or stale_process.killed)
        self.assertFalse(successor_process.terminated)
        self.assertIn("fence expired", stale.snapshot()["last_error"])


class TestRedisFencedLeaderLease(_RealRedisDockerTestCase):
    def _store(self):
        from paper_fleet_reconciler import RedisFencedLeaderStore

        return RedisFencedLeaderStore(
            self.redis,
            lease_key="pantheon:test:l12-cap:leader",
        )

    def test_two_real_reconciler_processes_converge_and_stale_token_is_fenced(self):
        context = multiprocessing.get_context("spawn")
        result_queue = context.Queue()
        contenders = [
            context.Process(
                target=_redis_lease_process,
                args=(
                    self.redis_url,
                    f"reconciler-process-{index}",
                    0.4,
                    result_queue,
                ),
            )
            for index in (1, 2)
        ]
        for process in contenders:
            process.start()
        for process in contenders:
            process.join(timeout=10)
            self.assertEqual(process.exitcode, 0)

        first_round = [result_queue.get(timeout=2) for _ in contenders]
        leaders = [item for item in first_round if item["acquired"]]
        followers = [item for item in first_round if not item["acquired"]]
        self.assertEqual(len(leaders), 1)
        self.assertEqual(len(followers), 1)
        first_token = leaders[0]["token"]

        time.sleep(0.45)
        successor = context.Process(
            target=_redis_lease_process,
            args=(
                self.redis_url,
                "reconciler-process-successor",
                0.4,
                result_queue,
            ),
        )
        successor.start()
        successor.join(timeout=10)
        self.assertEqual(successor.exitcode, 0)
        successor_result = result_queue.get(timeout=2)
        self.assertTrue(successor_result["acquired"])
        self.assertGreater(successor_result["token"], first_token)

        stale_renewal = self._store().acquire_or_renew(
            leaders[0]["reconciler_id"],
            first_token,
            0.4,
        )
        self.assertFalse(stale_renewal.acquired)
        self.assertEqual(stale_renewal.token, successor_result["token"])
        self.assertFalse(
            self._store().validate(
                leaders[0]["reconciler_id"],
                first_token,
            )
        )

    def test_production_builder_uses_shared_redis_fenced_backend(self) -> None:
        import paper_fleet_reconciler

        with patch.dict(
            "os.environ",
            {
                "RECONCILER_LEADER_REDIS_URL": self.redis_url,
                "RECONCILER_LEADER_LEASE_PATH": "",
            },
            clear=False,
        ):
            store = paper_fleet_reconciler._build_production_leader_store()
        self.assertEqual(store.kind, "redis_fenced_leader_store")


class TestPaperFleetStaleSessionAdmissionAndResume(unittest.TestCase):
    """
    SD-PAPER-01: Bounded paper session on manual Source data.
    Tests pause and resume lifecycle transitions driven by market snapshot freshness.
    """

    def setUp(self) -> None:
        self.transitions: List[Dict[str, Any]] = []

    def _make_store_and_recon(
        self,
        bindings: List[Dict[str, Any]],
        *,
        source_snapshot: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Any, Any]:
        from services.runtime_manager.runtime_binding import RuntimeBinding, RuntimeBindingStore
        from services.paper_fleet_reconciler.paper_fleet_reconciler import PaperFleetReconciler

        store = RuntimeBindingStore()
        known = {
            "binding_id",
            "runtime_id",
            "capital_pool_id",
            "artifact_id",
            "artifact_version",
            "plan_id",
            "persona_capital_binding_id",
            "deployment_mode",
            "status",
            "effective_at",
            "retired_at",
            "rollback_parent",
            "metadata",
        }
        for b_dict in bindings:
            meta = dict(b_dict.get("metadata") or {})
            for k, v in b_dict.items():
                if k not in known:
                    meta[k] = v
            clean_dict = {k: v for k, v in b_dict.items() if k in known}
            clean_dict["metadata"] = meta
            clean_dict.setdefault("effective_at", datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
            rb = RuntimeBinding(**clean_dict)
            store.create(rb, single_runtime_enforced=False)

        transitions = self.transitions

        class _MockReconciler(PaperFleetReconciler):
            def _spawn(inner_self, binding_id: str, port: int, env: Any) -> _FakeProcess:
                proc = _FakeProcess(pid=100)
                return proc

            def _resolve_market_snapshot(inner_self, binding: Dict[str, Any]) -> Optional[Dict[str, Any]]:
                if source_snapshot is not None:
                    return source_snapshot
                return super()._resolve_market_snapshot(binding)

            def _transition_binding(
                inner_self,
                binding_id: str,
                new_status: str,
                *,
                metadata_patch: Optional[Dict[str, Any]] = None,
            ) -> bool:
                transitions.append({
                    "binding_id": binding_id,
                    "new_status": new_status,
                    "metadata_patch": metadata_patch,
                })
                return super()._transition_binding(
                    binding_id,
                    new_status,
                    metadata_patch=metadata_patch,
                )

        recon = _MockReconciler(
            store=store,
            leader_store=_unit_leader_store(),
            poll_interval_seconds=999,
            restart_backoff_seconds=0,
            drain_timeout_seconds=1,
        )
        return store, recon

    def test_fresh_snapshot_retains_and_starts_active_worker(self) -> None:
        now = datetime.now(timezone.utc)
        fresh_snap = {
            "snapshot_id": "snap-fresh-001",
            "symbol": "AAPL.US",
            "event_time": (now - timedelta(seconds=60)).isoformat().replace("+00:00", "Z"),
            "observed_at": now.isoformat().replace("+00:00", "Z"),
            "source_ref": "source-ingest://normalized/us-price/AAPL",
            "lineage": {"source": "manual_test"},
            "closes": [150.0, 152.0, 153.5],
        }
        b = _make_binding(
            "b-fresh-001",
            symbol="AAPL.US",
            market_data_policy={"owner": "source-ingest", "contract": "latest_stored_normalized", "max_age_seconds": 86400, "minimum_closes": 2},
            market_input=fresh_snap,
        )
        store, recon = self._make_store_and_recon([b], source_snapshot=fresh_snap)

        snap = recon.reconcile_once()
        self.assertEqual(snap["worker_count"], 1)
        self.assertEqual(snap["running_count"], 1)
        self.assertEqual(len(self.transitions), 0)

        # Second cycle retains worker
        snap2 = recon.reconcile_once()
        self.assertEqual(snap2["worker_count"], 1)
        self.assertEqual(snap2["running_count"], 1)
        self.assertEqual(len(self.transitions), 0)

    def test_stale_snapshot_transitions_active_to_pending_pause_to_paused_and_stops_worker(self) -> None:
        now = datetime.now(timezone.utc)
        stale_snap = {
            "snapshot_id": "snap-stale-001",
            "symbol": "AAPL.US",
            "event_time": (now - timedelta(seconds=100000)).isoformat().replace("+00:00", "Z"),
            "observed_at": now.isoformat().replace("+00:00", "Z"),
            "source_ref": "source-ingest://normalized/us-price/AAPL",
            "lineage": {"source": "manual_test"},
            "closes": [150.0, 152.0],
        }
        b = _make_binding(
            "b-stale-001",
            symbol="AAPL.US",
            market_data_policy={"owner": "source-ingest", "contract": "latest_stored_normalized", "max_age_seconds": 86400, "minimum_closes": 2},
            market_input=stale_snap,
        )
        store, recon = self._make_store_and_recon([b], source_snapshot=stale_snap)

        snap = recon.reconcile_once()
        self.assertEqual(snap["worker_count"], 0)
        self.assertEqual(snap["running_count"], 0)

        # Verified status transitions: active -> pending_pause -> paused
        self.assertEqual(len(self.transitions), 2)
        self.assertEqual(self.transitions[0]["new_status"], "pending_pause")
        self.assertEqual(self.transitions[1]["new_status"], "paused")

        # Verified structured metadata
        saved = store.get("b-stale-001")
        self.assertIsNotNone(saved)
        self.assertEqual(saved.status, "paused")
        adm = saved.metadata.get("session_admission")
        self.assertIsNotNone(adm)
        self.assertEqual(adm["reason_code"], "market_input_stale")
        self.assertEqual(adm["source_snapshot_id"], "snap-stale-001")
        self.assertEqual(adm["max_age_seconds"], 86400)
        self.assertIsNone(adm["resume_snapshot_id"])
        self.assertIsNone(adm["resumed_at"])

    def test_repeated_stale_reconciles_are_idempotent(self) -> None:
        now = datetime.now(timezone.utc)
        stale_snap = {
            "snapshot_id": "snap-stale-001",
            "symbol": "AAPL.US",
            "event_time": (now - timedelta(seconds=100000)).isoformat().replace("+00:00", "Z"),
            "observed_at": now.isoformat().replace("+00:00", "Z"),
            "source_ref": "source-ref",
            "lineage": {"source": "manual_test"},
            "closes": [150.0, 152.0],
        }
        b = _make_binding(
            "b-stale-idemp",
            status="paused",
            symbol="AAPL.US",
            market_data_policy={"owner": "source-ingest", "contract": "latest_stored_normalized", "max_age_seconds": 86400, "minimum_closes": 2},
            market_input=stale_snap,
            metadata={
                "session_admission": {
                    "reason_code": "market_input_stale",
                    "source_snapshot_id": "snap-stale-001",
                    "source_event_time": stale_snap["event_time"],
                    "observed_at": now.isoformat(),
                    "max_age_seconds": 86400,
                    "pause_command_ref": "cmd-001",
                    "resume_snapshot_id": None,
                    "resumed_at": None,
                }
            },
        )
        store, recon = self._make_store_and_recon([b], source_snapshot=stale_snap)

        # Run 3 consecutive reconcile cycles
        for _ in range(3):
            snap = recon.reconcile_once()
            self.assertEqual(snap["worker_count"], 0)
            self.assertEqual(snap["running_count"], 0)

        # Zero new transitions performed
        self.assertEqual(len(self.transitions), 0)
        self.assertEqual(store.get("b-stale-idemp").status, "paused")

    def test_new_admitted_snapshot_resumes_paused_binding_once(self) -> None:
        now = datetime.now(timezone.utc)
        old_time = (now - timedelta(seconds=100000)).isoformat().replace("+00:00", "Z")
        new_time = (now - timedelta(seconds=120)).isoformat().replace("+00:00", "Z")

        new_snap = {
            "snapshot_id": "snap-fresh-002",
            "symbol": "AAPL.US",
            "event_time": new_time,
            "observed_at": now.isoformat().replace("+00:00", "Z"),
            "source_ref": "source-ingest://normalized/us-price/AAPL",
            "lineage": {"source": "manual_pull"},
            "closes": [155.0, 156.0, 157.0],
        }

        b = _make_binding(
            "b-resume-001",
            status="paused",
            symbol="AAPL.US",
            market_data_policy={"owner": "source-ingest", "contract": "latest_stored_normalized", "max_age_seconds": 86400, "minimum_closes": 2},
            metadata={
                "session_admission": {
                    "reason_code": "market_input_stale",
                    "source_snapshot_id": "snap-old-001",
                    "source_event_time": old_time,
                    "observed_at": old_time,
                    "max_age_seconds": 86400,
                    "pause_command_ref": "cmd-pause-001",
                    "resume_snapshot_id": None,
                    "resumed_at": None,
                }
            },
        )
        store, recon = self._make_store_and_recon([b], source_snapshot=new_snap)

        snap = recon.reconcile_once()
        self.assertEqual(snap["worker_count"], 1)
        self.assertEqual(snap["running_count"], 1)

        # Transition paused -> active recorded
        self.assertEqual(len(self.transitions), 1)
        self.assertEqual(self.transitions[0]["new_status"], "active")

        # Resume metadata recorded
        saved = store.get("b-resume-001")
        self.assertEqual(saved.status, "active")
        adm = saved.metadata["session_admission"]
        self.assertEqual(adm["source_snapshot_id"], "snap-old-001")
        self.assertEqual(adm["resume_snapshot_id"], "snap-fresh-002")
        self.assertIsNotNone(adm["resumed_at"])

    def test_same_stale_snapshot_cannot_resume(self) -> None:
        now = datetime.now(timezone.utc)
        snap_time = (now - timedelta(seconds=120)).isoformat().replace("+00:00", "Z")
        same_snap = {
            "snapshot_id": "snap-same-001",
            "symbol": "AAPL.US",
            "event_time": snap_time,
            "observed_at": now.isoformat().replace("+00:00", "Z"),
            "source_ref": "source-ref",
            "lineage": {"source": "manual"},
            "closes": [150.0, 151.0],
        }
        b = _make_binding(
            "b-same-snap",
            status="paused",
            symbol="AAPL.US",
            market_data_policy={"owner": "source-ingest", "contract": "latest_stored_normalized", "max_age_seconds": 86400, "minimum_closes": 2},
            metadata={
                "session_admission": {
                    "reason_code": "market_input_stale",
                    "source_snapshot_id": "snap-same-001",
                    "source_event_time": snap_time,
                    "observed_at": snap_time,
                    "max_age_seconds": 86400,
                    "pause_command_ref": "cmd-001",
                    "resume_snapshot_id": None,
                    "resumed_at": None,
                }
            },
        )
        store, recon = self._make_store_and_recon([b], source_snapshot=same_snap)

        snap = recon.reconcile_once()
        self.assertEqual(snap["worker_count"], 0)
        self.assertEqual(len(self.transitions), 0)
        self.assertEqual(store.get("b-same-snap").status, "paused")

    def test_older_or_same_timestamp_snapshot_cannot_resume(self) -> None:
        now = datetime.now(timezone.utc)
        old_time = (now - timedelta(seconds=5000)).isoformat().replace("+00:00", "Z")
        older_time = (now - timedelta(seconds=10000)).isoformat().replace("+00:00", "Z")

        older_snap = {
            "snapshot_id": "snap-diff-id-001",
            "symbol": "AAPL.US",
            "event_time": older_time,
            "observed_at": now.isoformat().replace("+00:00", "Z"),
            "source_ref": "source-ref",
            "lineage": {"source": "manual"},
            "closes": [150.0, 151.0],
        }
        b = _make_binding(
            "b-older-snap",
            status="paused",
            symbol="AAPL.US",
            market_data_policy={"owner": "source-ingest", "contract": "latest_stored_normalized", "max_age_seconds": 86400, "minimum_closes": 2},
            metadata={
                "session_admission": {
                    "reason_code": "market_input_stale",
                    "source_snapshot_id": "snap-orig-001",
                    "source_event_time": old_time,
                    "observed_at": old_time,
                    "max_age_seconds": 86400,
                    "pause_command_ref": "cmd-001",
                    "resume_snapshot_id": None,
                    "resumed_at": None,
                }
            },
        )
        store, recon = self._make_store_and_recon([b], source_snapshot=older_snap)

        snap = recon.reconcile_once()
        self.assertEqual(snap["worker_count"], 0)
        self.assertEqual(len(self.transitions), 0)
        self.assertEqual(store.get("b-older-snap").status, "paused")

    def test_invalid_or_future_or_wrong_scope_snapshot_cannot_resume(self) -> None:
        now = datetime.now(timezone.utc)
        old_time = (now - timedelta(seconds=50000)).isoformat().replace("+00:00", "Z")

        # Future snapshot (>300s)
        future_snap = {
            "snapshot_id": "snap-future-001",
            "symbol": "AAPL.US",
            "event_time": (now + timedelta(seconds=1000)).isoformat().replace("+00:00", "Z"),
            "observed_at": now.isoformat().replace("+00:00", "Z"),
            "source_ref": "source-ref",
            "lineage": {"source": "manual"},
            "closes": [150.0, 151.0],
        }
        b = _make_binding(
            "b-invalid-resume",
            status="paused",
            symbol="AAPL.US",
            market_data_policy={"owner": "source-ingest", "contract": "latest_stored_normalized", "max_age_seconds": 86400, "minimum_closes": 2},
            metadata={
                "session_admission": {
                    "reason_code": "market_input_stale",
                    "source_snapshot_id": "snap-orig-001",
                    "source_event_time": old_time,
                    "observed_at": old_time,
                    "max_age_seconds": 86400,
                    "pause_command_ref": "cmd-001",
                    "resume_snapshot_id": None,
                    "resumed_at": None,
                }
            },
        )
        store, recon = self._make_store_and_recon([b], source_snapshot=future_snap)
        snap = recon.reconcile_once()
        self.assertEqual(snap["worker_count"], 0)
        self.assertEqual(len(self.transitions), 0)

        # Malformed closes snapshot
        malformed_snap = {
            "snapshot_id": "snap-malformed-001",
            "symbol": "AAPL.US",
            "event_time": (now - timedelta(seconds=60)).isoformat().replace("+00:00", "Z"),
            "observed_at": now.isoformat().replace("+00:00", "Z"),
            "source_ref": "source-ref",
            "lineage": {"source": "manual"},
            "closes": ["not_a_number"],
        }
        store2, recon2 = self._make_store_and_recon([b], source_snapshot=malformed_snap)
        snap2 = recon2.reconcile_once()
        self.assertEqual(snap2["worker_count"], 0)
        self.assertEqual(len(self.transitions), 0)

        # Wrong symbol snapshot
        wrong_sym_snap = {
            "snapshot_id": "snap-wrong-001",
            "symbol": "MSFT.US",
            "event_time": (now - timedelta(seconds=60)).isoformat().replace("+00:00", "Z"),
            "observed_at": now.isoformat().replace("+00:00", "Z"),
            "source_ref": "source-ref",
            "lineage": {"source": "manual"},
            "closes": [150.0, 151.0],
        }
        store3, recon3 = self._make_store_and_recon([b], source_snapshot=wrong_sym_snap)
        snap3 = recon3.reconcile_once()
        self.assertEqual(snap3["worker_count"], 0)
        self.assertEqual(len(self.transitions), 0)

    def test_restart_while_paused_stays_paused_starts_no_worker(self) -> None:
        now = datetime.now(timezone.utc)
        old_time = (now - timedelta(seconds=100000)).isoformat().replace("+00:00", "Z")
        stale_snap = {
            "snapshot_id": "snap-stale-001",
            "symbol": "AAPL.US",
            "event_time": old_time,
            "observed_at": old_time,
            "source_ref": "source-ref",
            "lineage": {"source": "manual"},
            "closes": [150.0, 151.0],
        }
        b = _make_binding(
            "b-restart-paused",
            status="paused",
            symbol="AAPL.US",
            market_data_policy={"owner": "source-ingest", "contract": "latest_stored_normalized", "max_age_seconds": 86400, "minimum_closes": 2},
            metadata={
                "session_admission": {
                    "reason_code": "market_input_stale",
                    "source_snapshot_id": "snap-stale-001",
                    "source_event_time": old_time,
                    "observed_at": old_time,
                    "max_age_seconds": 86400,
                    "pause_command_ref": "cmd-001",
                    "resume_snapshot_id": None,
                    "resumed_at": None,
                }
            },
        )
        store, recon1 = self._make_store_and_recon([b], source_snapshot=stale_snap)
        recon1.reconcile_once()

        # Simulate fresh process restart by creating a new reconciler instance against the same store
        from paper_fleet_reconciler import PaperFleetReconciler
        recon2 = PaperFleetReconciler(
            store=store,
            poll_interval_seconds=999,
            restart_backoff_seconds=0,
            drain_timeout_seconds=1,
        )
        snap = recon2.reconcile_once()
        self.assertEqual(snap["worker_count"], 0)
        self.assertEqual(snap["running_count"], 0)
        self.assertEqual(store.get("b-restart-paused").status, "paused")

    def test_operator_pause_is_never_auto_resumed(self) -> None:
        now = datetime.now(timezone.utc)
        fresh_snap = {
            "snapshot_id": "snap-fresh-001",
            "symbol": "AAPL.US",
            "event_time": (now - timedelta(seconds=60)).isoformat().replace("+00:00", "Z"),
            "observed_at": now.isoformat().replace("+00:00", "Z"),
            "source_ref": "source-ref",
            "lineage": {"source": "manual"},
            "closes": [150.0, 151.0],
        }
        # Operator pause has no session_admission or non-stale reason
        b1 = _make_binding(
            "b-op-pause-1",
            status="paused",
            symbol="AAPL.US",
            market_data_policy={"owner": "source-ingest", "contract": "latest_stored_normalized", "max_age_seconds": 86400, "minimum_closes": 2},
            metadata={"operator_note": "manual audit pause"},
        )
        b2 = _make_binding(
            "b-op-pause-2",
            status="paused",
            symbol="AAPL.US",
            market_data_policy={"owner": "source-ingest", "contract": "latest_stored_normalized", "max_age_seconds": 86400, "minimum_closes": 2},
            metadata={
                "session_admission": {
                    "reason_code": "operator_requested_pause",
                    "source_snapshot_id": "snap-001",
                }
            },
        )
        store, recon = self._make_store_and_recon([b1, b2], source_snapshot=fresh_snap)

        snap = recon.reconcile_once()
        self.assertEqual(snap["worker_count"], 0)
        self.assertEqual(len(self.transitions), 0)
        self.assertEqual(store.get("b-op-pause-1").status, "paused")
        self.assertEqual(store.get("b-op-pause-2").status, "paused")

class TestPaperFleetTaiwanSessionFreshness(unittest.TestCase):
    """Governed Taiwan (Asia/Taipei) market-session freshness at the fleet
    reconciler's admission defense (services.execution.market_snapshot_admission),
    replacing the flat 24h age gate for TWSE/TPEx official closes."""

    def setUp(self) -> None:
        self.transitions: List[Dict[str, Any]] = []

    def _make_store_and_recon(
        self,
        bindings: List[Dict[str, Any]],
        *,
        source_snapshot: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Any, Any]:
        from services.runtime_manager.runtime_binding import RuntimeBinding, RuntimeBindingStore
        from services.paper_fleet_reconciler.paper_fleet_reconciler import PaperFleetReconciler

        store = RuntimeBindingStore()
        known = {
            "binding_id", "runtime_id", "capital_pool_id", "artifact_id",
            "artifact_version", "plan_id", "persona_capital_binding_id",
            "deployment_mode", "status", "effective_at", "retired_at",
            "rollback_parent", "metadata",
        }
        for b_dict in bindings:
            meta = dict(b_dict.get("metadata") or {})
            for k, v in b_dict.items():
                if k not in known:
                    meta[k] = v
            clean_dict = {k: v for k, v in b_dict.items() if k in known}
            clean_dict["metadata"] = meta
            clean_dict.setdefault("effective_at", datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
            rb = RuntimeBinding(**clean_dict)
            store.create(rb, single_runtime_enforced=False)

        transitions = self.transitions

        class _MockReconciler(PaperFleetReconciler):
            def _spawn(inner_self, binding_id: str, port: int, env: Any) -> _FakeProcess:
                return _FakeProcess(pid=100)

            def _resolve_market_snapshot(inner_self, binding: Dict[str, Any]) -> Optional[Dict[str, Any]]:
                if source_snapshot is not None:
                    return source_snapshot
                return super()._resolve_market_snapshot(binding)

            def _transition_binding(
                inner_self,
                binding_id: str,
                new_status: str,
                *,
                metadata_patch: Optional[Dict[str, Any]] = None,
            ) -> bool:
                transitions.append({
                    "binding_id": binding_id,
                    "new_status": new_status,
                    "metadata_patch": metadata_patch,
                })
                return super()._transition_binding(
                    binding_id,
                    new_status,
                    metadata_patch=metadata_patch,
                )

        recon = _MockReconciler(
            store=store,
            leader_store=_unit_leader_store(),
            poll_interval_seconds=999,
            restart_backoff_seconds=0,
            drain_timeout_seconds=1,
        )
        return store, recon

    @staticmethod
    def _tw_snapshot(event_time: str, observed_at: str) -> Dict[str, Any]:
        return {
            "snapshot_id": "snap-tw-fleet-001",
            "symbol": "2330.TWSE",
            "event_time": event_time,
            "observed_at": observed_at,
            "source_ref": "source-ingest://normalized/tw-price/2330",
            "lineage": {
                "source_ids": ["tw-official:tw_price_daily:TWSE:2330:checksummed"],
                "connector_ids": ["tw-twse-tpex-official-market"],
            },
            "closes": [950.0, 955.0],
        }

    @patch(
        "services.paper_fleet_reconciler.paper_fleet_reconciler._iso_now",
        return_value="2026-08-29T12:00:00Z",
    )
    def test_tw_friday_close_retains_active_worker_on_saturday(self, _mock_now) -> None:
        snap = self._tw_snapshot("2026-08-28T05:30:00Z", "2026-08-29T11:00:00Z")
        b = _make_binding(
            "b-tw-weekend-001",
            symbol="2330.TWSE",
            market_data_policy={"owner": "source-ingest", "contract": "latest_stored_normalized", "max_age_seconds": 86400, "minimum_closes": 2},
            market_input=snap,
        )
        store, recon = self._make_store_and_recon([b], source_snapshot=snap)

        result = recon.reconcile_once()
        self.assertEqual(result["worker_count"], 1)
        self.assertEqual(result["running_count"], 1)
        self.assertEqual(len(self.transitions), 0)

    @patch(
        "services.paper_fleet_reconciler.paper_fleet_reconciler._iso_now",
        return_value="2026-08-31T06:00:00Z",
    )
    def test_tw_friday_close_paused_once_monday_session_closes(self, _mock_now) -> None:
        snap = self._tw_snapshot("2026-08-28T05:30:00Z", "2026-08-31T05:45:00Z")
        b = _make_binding(
            "b-tw-monday-stale-001",
            symbol="2330.TWSE",
            market_data_policy={"owner": "source-ingest", "contract": "latest_stored_normalized", "max_age_seconds": 86400, "minimum_closes": 2},
            market_input=snap,
        )
        store, recon = self._make_store_and_recon([b], source_snapshot=snap)

        result = recon.reconcile_once()
        self.assertEqual(result["worker_count"], 0)
        self.assertEqual(len(self.transitions), 2)
        self.assertEqual(self.transitions[1]["new_status"], "paused")
        saved = store.get("b-tw-monday-stale-001")
        adm = saved.metadata.get("session_admission")
        self.assertEqual(adm["reason_code"], "market_input_stale")



if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    unittest.main()


"""Deterministic concurrency tests for non-blocking state reads in PaperFleetReconciler."""
from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE_DIR = Path(__file__).resolve().parent
for _p in (REPO_ROOT, SERVICE_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from services.paper_fleet_reconciler.paper_fleet_reconciler import (  # noqa: E402
    InMemoryFencedLeaderStore,
    PaperFleetReconciler,
    _Handler,
)
from services.runtime_manager.runtime_binding import (  # noqa: E402
    RuntimeBinding,
    RuntimeBindingStore,
)


class _EventProcess:
    """Subprocess stub whose wait() can optionally block on a threading.Event."""

    def __init__(
        self,
        pid: int = 100,
        *,
        wait_entered: Optional[threading.Event] = None,
        release_wait: Optional[threading.Event] = None,
    ) -> None:
        self.pid = pid
        self._returncode: Optional[int] = None
        self.terminated = False
        self.killed = False
        self._wait_entered = wait_entered
        self._release_wait = release_wait

    @property
    def returncode(self) -> Optional[int]:
        return self._returncode

    def poll(self) -> Optional[int]:
        return self._returncode

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True
        self._returncode = -9

    def wait(self, timeout: float = 0) -> int:
        if self._wait_entered is not None:
            self._wait_entered.set()
        if self._release_wait is not None:
            if not self._release_wait.wait(timeout=5):
                raise RuntimeError("timed out waiting for release_wait event in test")
        if self._returncode is None:
            self._returncode = -15 if self.terminated else 0
        return self._returncode


def _make_binding_record(
    binding_id: str,
    *,
    runtime_id: str = "rt-001",
    status: str = "active",
    symbol: str = "AAPL.US",
) -> RuntimeBinding:
    now_iso = (
        (datetime.now(timezone.utc) - timedelta(seconds=600))
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    return RuntimeBinding(
        binding_id=binding_id,
        runtime_id=runtime_id,
        capital_pool_id="pool-a",
        artifact_id="art-001",
        artifact_version="1.0.0",
        plan_id="plan-001",
        persona_capital_binding_id="pcb-001",
        deployment_mode="paper",
        status=status,
        effective_at=now_iso,
        metadata={
            "symbol": symbol,
            "market_data_policy": {
                "owner": "source-ingest",
                "contract": "latest_stored_normalized",
                "max_age_seconds": 86400,
                "minimum_closes": 2,
            },
        },
    )


def _fresh_snapshot(symbol: str = "AAPL.US", snapshot_id: str = "snap-fresh") -> Dict[str, Any]:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return {
        "snapshot_id": snapshot_id,
        "symbol": symbol,
        "event_time": (now - timedelta(seconds=60)).isoformat().replace("+00:00", "Z"),
        "observed_at": now.isoformat().replace("+00:00", "Z"),
        "source_ref": f"source-ingest://normalized/us-price/{symbol}",
        "lineage": {"source": "unit_test"},
        "closes": [150.0, 152.0],
    }


def _stale_snapshot(symbol: str = "AAPL.US", snapshot_id: str = "snap-stale") -> Dict[str, Any]:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return {
        "snapshot_id": snapshot_id,
        "symbol": symbol,
        "event_time": (now - timedelta(seconds=200000)).isoformat().replace("+00:00", "Z"),
        "observed_at": now.isoformat().replace("+00:00", "Z"),
        "source_ref": f"source-ingest://normalized/us-price/{symbol}",
        "lineage": {"source": "unit_test"},
        "closes": [150.0, 152.0],
    }


class TestPaperFleetReconcilerStateReadNoCycleLock(unittest.TestCase):
    """Verify state reads never block on reconcile cycle network/process waits."""

    def _assert_consistent_worker_session_view(self, snap: Dict[str, Any]) -> None:
        workers = snap["workers"]
        sessions_by_id = {s["session_id"]: s for s in snap["monitoring_sessions"]}
        active_sessions = [s for s in snap["monitoring_sessions"] if s.get("active")]
        self.assertEqual(snap["worker_count"], len(workers))
        self.assertEqual(
            snap["running_count"],
            sum(1 for w in workers if w["status"] == "running"),
        )
        self.assertEqual(snap["active_monitoring_session_count"], len(active_sessions))
        for worker in workers:
            if worker["status"] == "running":
                session_id = worker["monitoring_session_id"]
                self.assertIn(session_id, sessions_by_id)
                self.assertTrue(
                    sessions_by_id[session_id]["active"],
                    f"running worker {worker['binding_id']} has non-active session {session_id}",
                )

    def _http_get_json(self, recon: PaperFleetReconciler, path: str) -> tuple[int, Dict[str, Any]]:
        with patch(
            "services.paper_fleet_reconciler.paper_fleet_reconciler.get_reconciler",
            return_value=recon,
        ):
            server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
            port = server.server_address[1]
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            try:
                url = f"http://127.0.0.1:{port}{path}"
                req = urllib.request.Request(url, method="GET")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    return resp.status, json.loads(resp.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()
                server_thread.join(timeout=5)

    def test_state_reads_return_while_cycle_blocked_in_source_snapshot_fetch(self) -> None:
        store = RuntimeBindingStore()
        store.create(_make_binding_record("b-fetch-001", runtime_id="rt-fetch-001"), single_runtime_enforced=False)

        fetch_entered = threading.Event()
        release_fetch = threading.Event()
        block_next_fetch = False
        next_snapshot = _fresh_snapshot(snapshot_id="snap-fetch-1")

        class _Reconciler(PaperFleetReconciler):
            def _spawn(inner_self, binding_id: str, port: int, env: Any) -> _EventProcess:
                return _EventProcess(pid=201)

            def _fetch_source_snapshot(inner_self, symbol: str) -> Optional[Dict[str, Any]]:
                if block_next_fetch:
                    fetch_entered.set()
                    if not release_fetch.wait(timeout=5):
                        raise RuntimeError("timed out waiting for release_fetch event")
                return dict(next_snapshot)

        recon = _Reconciler(
            store=store,
            source_ingest_url="http://source-ingest:8097",
            leader_store=InMemoryFencedLeaderStore(),
            poll_interval_seconds=999,
            worker_base_port=9100,
            market_input_bootstrap_grace_seconds=0.0,
        )

        # Cycle 1 completes normally and starts the worker for b-fetch-001.
        first = recon.reconcile_once()
        self.assertEqual(first["cycle_count"], 1)
        self.assertEqual(first["worker_count"], 1)
        self.assertEqual(first["running_count"], 1)
        self._assert_consistent_worker_session_view(first)

        # Add a second binding and block Cycle 2 inside _fetch_source_snapshot.
        store.create(
            _make_binding_record("b-fetch-002", runtime_id="rt-fetch-002", symbol="MSFT.US"),
            single_runtime_enforced=False,
        )
        block_next_fetch = True
        next_snapshot = _fresh_snapshot(symbol="MSFT.US", snapshot_id="snap-fetch-2")

        def _fetch_by_symbol(symbol: str) -> Optional[Dict[str, Any]]:
            if symbol == "MSFT.US" and block_next_fetch:
                fetch_entered.set()
                if not release_fetch.wait(timeout=5):
                    raise RuntimeError("timed out waiting for release_fetch event")
            return _fresh_snapshot(symbol=symbol, snapshot_id=f"snap-{symbol}")

        with patch.object(recon, "_fetch_source_snapshot", side_effect=_fetch_by_symbol):
            cycle_thread = threading.Thread(target=recon.reconcile_once, daemon=True)
            cycle_thread.start()
            self.assertTrue(fetch_entered.wait(timeout=5))
            self.assertFalse(release_fetch.is_set())

            # State reads on another thread must return before release_fetch is set.
            read_results: Dict[str, Any] = {}

            def _reader() -> None:
                read_results["snapshot"] = recon.snapshot()
                read_results["fleet_state"] = self._http_get_json(recon, "/api/fleet/state")
                read_results["readyz"] = self._http_get_json(recon, "/readyz")
                read_results["healthz"] = self._http_get_json(recon, "/healthz")
                read_results["before_release"] = not release_fetch.is_set()

            reader_thread = threading.Thread(target=_reader, daemon=True)
            reader_thread.start()
            reader_thread.join(timeout=5)
            self.assertFalse(reader_thread.is_alive())
            self.assertTrue(read_results.get("before_release"))

            snap_during = read_results["snapshot"]
            self.assertEqual(snap_during["cycle_count"], 1)
            self.assertEqual(snap_during["worker_count"], 1)
            self._assert_consistent_worker_session_view(snap_during)

            state_code, state_body = read_results["fleet_state"]
            self.assertEqual(state_code, 200)
            self.assertEqual(state_body["cycle_count"], 1)
            self.assertEqual(state_body["worker_count"], 1)
            self._assert_consistent_worker_session_view(state_body)

            ready_code, ready_body = read_results["readyz"]
            self.assertEqual(ready_code, 200)
            self.assertTrue(ready_body["ready"])
            self.assertEqual(ready_body["cycle_count"], 1)

            health_code, health_body = read_results["healthz"]
            self.assertEqual(health_code, 200)
            self.assertTrue(health_body["ready"])

            release_fetch.set()
            cycle_thread.join(timeout=5)
            self.assertFalse(cycle_thread.is_alive())

        # After the event is set, the next read reflects the completed cycle's result.
        snap_after = recon.snapshot()
        self.assertEqual(snap_after["cycle_count"], 2)
        self.assertEqual(snap_after["worker_count"], 2)
        self.assertEqual(snap_after["running_count"], 2)
        self._assert_consistent_worker_session_view(snap_after)

        state_code_after, state_body_after = self._http_get_json(recon, "/api/fleet/state")
        self.assertEqual(state_code_after, 200)
        self.assertEqual(state_body_after["cycle_count"], 2)
        self.assertEqual(state_body_after["worker_count"], 2)
        self._assert_consistent_worker_session_view(state_body_after)

    def test_state_reads_return_while_cycle_blocked_in_runtime_manager_transition(self) -> None:
        store = RuntimeBindingStore()
        store.create(_make_binding_record("b-trans-001", runtime_id="rt-trans-001"), single_runtime_enforced=False)

        transition_entered = threading.Event()
        release_transition = threading.Event()
        current_snapshot = [_fresh_snapshot(snapshot_id="snap-trans-fresh")]

        class _Reconciler(PaperFleetReconciler):
            def _spawn(inner_self, binding_id: str, port: int, env: Any) -> _EventProcess:
                return _EventProcess(pid=301)

            def _fetch_source_snapshot(inner_self, symbol: str) -> Optional[Dict[str, Any]]:
                return dict(current_snapshot[0])

            def _transition_binding(
                inner_self,
                binding_id: str,
                new_status: str,
                *,
                metadata_patch: Optional[Dict[str, Any]] = None,
            ) -> bool:
                if new_status == "pending_pause":
                    transition_entered.set()
                    if not release_transition.wait(timeout=5):
                        raise RuntimeError("timed out waiting for release_transition event")
                return super()._transition_binding(
                    binding_id,
                    new_status,
                    metadata_patch=metadata_patch,
                )

        recon = _Reconciler(
            store=store,
            source_ingest_url="http://source-ingest:8097",
            leader_store=InMemoryFencedLeaderStore(),
            poll_interval_seconds=999,
            worker_base_port=9200,
            market_input_bootstrap_grace_seconds=0.0,
        )

        # Cycle 1 starts the worker for b-trans-001.
        first = recon.reconcile_once()
        self.assertEqual(first["cycle_count"], 1)
        self.assertEqual(first["worker_count"], 1)
        self.assertEqual(first["running_count"], 1)
        self._assert_consistent_worker_session_view(first)

        # Cycle 2 sees a stale snapshot and blocks inside _transition_binding.
        current_snapshot[0] = _stale_snapshot(snapshot_id="snap-trans-stale")
        cycle_thread = threading.Thread(target=recon.reconcile_once, daemon=True)
        cycle_thread.start()
        self.assertTrue(transition_entered.wait(timeout=5))
        self.assertFalse(release_transition.is_set())

        read_results: Dict[str, Any] = {}

        def _reader() -> None:
            read_results["snapshot"] = recon.snapshot()
            read_results["fleet_state"] = self._http_get_json(recon, "/api/fleet/state")
            read_results["readyz"] = self._http_get_json(recon, "/readyz")
            read_results["before_release"] = not release_transition.is_set()

        reader_thread = threading.Thread(target=_reader, daemon=True)
        reader_thread.start()
        reader_thread.join(timeout=5)
        self.assertFalse(reader_thread.is_alive())
        self.assertTrue(read_results.get("before_release"))

        # While blocked in transition, the worker is not yet terminated and its
        # monitoring session is still open (no torn view).
        snap_during = read_results["snapshot"]
        self.assertEqual(snap_during["cycle_count"], 1)
        self.assertEqual(snap_during["worker_count"], 1)
        self.assertEqual(snap_during["running_count"], 1)
        self.assertEqual(snap_during["active_monitoring_session_count"], 1)
        self._assert_consistent_worker_session_view(snap_during)

        state_code, state_body = read_results["fleet_state"]
        self.assertEqual(state_code, 200)
        self.assertEqual(state_body["cycle_count"], 1)
        self.assertEqual(state_body["worker_count"], 1)
        self._assert_consistent_worker_session_view(state_body)

        ready_code, ready_body = read_results["readyz"]
        self.assertEqual(ready_code, 200)
        self.assertTrue(ready_body["ready"])

        release_transition.set()
        cycle_thread.join(timeout=5)
        self.assertFalse(cycle_thread.is_alive())

        # After the event is set, the next read reflects the paused binding and stopped worker.
        snap_after = recon.snapshot()
        self.assertEqual(snap_after["cycle_count"], 2)
        self.assertEqual(snap_after["worker_count"], 0)
        self.assertEqual(snap_after["running_count"], 0)
        self.assertEqual(snap_after["active_monitoring_session_count"], 0)
        self.assertEqual(snap_after["ended_monitoring_session_count"], 1)
        self._assert_consistent_worker_session_view(snap_after)
        self.assertEqual(store.get("b-trans-001").status, "paused")

        state_code_after, state_body_after = self._http_get_json(recon, "/api/fleet/state")
        self.assertEqual(state_code_after, 200)
        self.assertEqual(state_body_after["cycle_count"], 2)
        self.assertEqual(state_body_after["worker_count"], 0)
        self.assertEqual(state_body_after["active_monitoring_session_count"], 0)

    def test_state_reads_return_while_cycle_blocked_in_spawn_terminate_and_loop_truth(self) -> None:
        store = RuntimeBindingStore()
        store.create(_make_binding_record("b-proc-001", runtime_id="rt-proc-001"), single_runtime_enforced=False)

        spawn_entered = threading.Event()
        release_spawn = threading.Event()
        wait_entered = threading.Event()
        release_wait = threading.Event()
        loop_write_entered = threading.Event()
        release_loop_write = threading.Event()

        block_spawn = False
        block_loop_write = False

        class _BlockingLoopWriter:
            def __init__(self) -> None:
                self.calls: List[Dict[str, Any]] = []

            async def record_success(self, **kwargs: Any) -> None:
                if block_loop_write:
                    loop_write_entered.set()
                    if not release_loop_write.wait(timeout=5):
                        raise RuntimeError("timed out waiting for release_loop_write event")
                self.calls.append(kwargs)

            async def record_failure(self, **kwargs: Any) -> None:
                self.calls.append(kwargs)

        writer = _BlockingLoopWriter()

        class _Reconciler(PaperFleetReconciler):
            def _spawn(inner_self, binding_id: str, port: int, env: Any) -> _EventProcess:
                if block_spawn:
                    spawn_entered.set()
                    if not release_spawn.wait(timeout=5):
                        raise RuntimeError("timed out waiting for release_spawn event")
                return _EventProcess(
                    pid=401,
                    wait_entered=wait_entered,
                    release_wait=release_wait,
                )

            def _fetch_source_snapshot(inner_self, symbol: str) -> Optional[Dict[str, Any]]:
                return _fresh_snapshot(symbol=symbol)

        recon = _Reconciler(
            store=store,
            source_ingest_url="http://source-ingest:8097",
            leader_store=InMemoryFencedLeaderStore(),
            poll_interval_seconds=999,
            worker_base_port=9300,
            market_input_bootstrap_grace_seconds=0.0,
            loop_writer=writer,
        )
        recon._loop_write_interval = 0.0

        # 1. Block inside _spawn during cycle 1.
        block_spawn = True
        t1 = threading.Thread(target=recon.reconcile_once, daemon=True)
        t1.start()
        self.assertTrue(spawn_entered.wait(timeout=5))

        # Reader sees 0 workers and 0 active monitoring sessions (no torn session opened early).
        snap_during_spawn = recon.snapshot()
        self.assertEqual(snap_during_spawn["worker_count"], 0)
        self.assertEqual(snap_during_spawn["active_monitoring_session_count"], 0)
        self._assert_consistent_worker_session_view(snap_during_spawn)
        release_spawn.set()
        t1.join(timeout=5)
        self.assertFalse(t1.is_alive())
        block_spawn = False

        snap_after_spawn = recon.snapshot()
        self.assertEqual(snap_after_spawn["worker_count"], 1)
        self.assertEqual(snap_after_spawn["active_monitoring_session_count"], 1)
        self._assert_consistent_worker_session_view(snap_after_spawn)

        # 2. Block inside loop-truth publish during cycle 2.
        block_loop_write = True
        t2 = threading.Thread(target=recon.reconcile_once, daemon=True)
        t2.start()
        self.assertTrue(loop_write_entered.wait(timeout=5))

        code_during_loop, body_during_loop = self._http_get_json(recon, "/api/fleet/state")
        self.assertEqual(code_during_loop, 200)
        self.assertFalse(release_loop_write.is_set())
        self._assert_consistent_worker_session_view(body_during_loop)
        release_loop_write.set()
        t2.join(timeout=5)
        self.assertFalse(t2.is_alive())
        block_loop_write = False
        self.assertEqual(recon.snapshot()["cycle_count"], 2)

        # 3. Retire binding so cycle 3 blocks inside process.wait() during _terminate_worker.
        store.transition_status("b-proc-001", "retired")
        t3 = threading.Thread(target=recon.reconcile_once, daemon=True)
        t3.start()
        self.assertTrue(wait_entered.wait(timeout=5))

        snap_during_terminate = recon.snapshot()
        self.assertFalse(release_wait.is_set())
        self.assertEqual(snap_during_terminate["worker_count"], 1)
        self.assertEqual(snap_during_terminate["active_monitoring_session_count"], 1)
        self._assert_consistent_worker_session_view(snap_during_terminate)

        release_wait.set()
        t3.join(timeout=5)
        self.assertFalse(t3.is_alive())

        snap_after_terminate = recon.snapshot()
        self.assertEqual(snap_after_terminate["cycle_count"], 3)
        self.assertEqual(snap_after_terminate["worker_count"], 0)
        self.assertEqual(snap_after_terminate["active_monitoring_session_count"], 0)
        self.assertEqual(snap_after_terminate["ended_monitoring_session_count"], 1)
        self._assert_consistent_worker_session_view(snap_after_terminate)

    def test_two_concurrent_cycles_cannot_both_mutate_worker_state(self) -> None:
        store = RuntimeBindingStore()
        store.create(_make_binding_record("b-conc-001", runtime_id="rt-conc-001"), single_runtime_enforced=False)

        cycle1_in_fetch = threading.Event()
        release_cycle1 = threading.Event()
        cycle2_entered_fetch_fleet = threading.Event()
        spawned: List[Dict[str, Any]] = []
        fetch_calls = 0
        active_cycles = 0
        max_concurrent_cycles = 0
        counter_lock = threading.Lock()

        class _Reconciler(PaperFleetReconciler):
            def _fetch_fleet_state(inner_self):
                nonlocal fetch_calls, active_cycles, max_concurrent_cycles
                with counter_lock:
                    fetch_calls += 1
                    call_num = fetch_calls
                    active_cycles += 1
                    if active_cycles > max_concurrent_cycles:
                        max_concurrent_cycles = active_cycles
                if call_num == 2:
                    cycle2_entered_fetch_fleet.set()
                return super()._fetch_fleet_state()

            def _fetch_source_snapshot(inner_self, symbol: str) -> Optional[Dict[str, Any]]:
                if not cycle1_in_fetch.is_set():
                    cycle1_in_fetch.set()
                    if not release_cycle1.wait(timeout=5):
                        raise RuntimeError("timed out waiting for release_cycle1 event")
                return _fresh_snapshot(symbol=symbol)

            def _spawn(inner_self, binding_id: str, port: int, env: Any) -> _EventProcess:
                proc = _EventProcess(pid=500 + len(spawned))
                spawned.append({"binding_id": binding_id, "port": port, "proc": proc})
                return proc

            def reconcile_once(inner_self, leader_store: Any | None = None) -> Dict[str, Any]:
                nonlocal active_cycles
                try:
                    return super().reconcile_once(leader_store=leader_store)
                finally:
                    with counter_lock:
                        if active_cycles > 0:
                            active_cycles -= 1

        recon = _Reconciler(
            store=store,
            source_ingest_url="http://source-ingest:8097",
            leader_store=InMemoryFencedLeaderStore(),
            poll_interval_seconds=999,
            worker_base_port=9400,
            market_input_bootstrap_grace_seconds=0.0,
        )

        t1 = threading.Thread(target=recon.reconcile_once, daemon=True)
        t1.start()
        self.assertTrue(cycle1_in_fetch.wait(timeout=5))

        t2 = threading.Thread(target=recon.reconcile_once, daemon=True)
        t2.start()

        # Cycle 2 is serialized behind _cycle_lock and must not enter _fetch_fleet_state
        # while Cycle 1 is still blocked inside _fetch_source_snapshot.
        self.assertFalse(cycle2_entered_fetch_fleet.wait(timeout=0.1))

        # Meanwhile state reads still complete without waiting for either cycle.
        code, body = self._http_get_json(recon, "/api/fleet/state")
        self.assertEqual(code, 200)
        self.assertEqual(body["cycle_count"], 0)
        self.assertFalse(release_cycle1.is_set())

        release_cycle1.set()
        t1.join(timeout=5)
        t2.join(timeout=5)
        self.assertFalse(t1.is_alive())
        self.assertFalse(t2.is_alive())

        self.assertEqual(max_concurrent_cycles, 1)
        self.assertEqual(len(spawned), 1)
        final_snap = recon.snapshot()
        self.assertEqual(final_snap["cycle_count"], 2)
        self.assertEqual(final_snap["worker_count"], 1)
        self.assertEqual(final_snap["running_count"], 1)
        self._assert_consistent_worker_session_view(final_snap)


if __name__ == "__main__":
    unittest.main()

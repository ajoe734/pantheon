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
from typing import Any, Callable, Dict, List, Optional
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
    """Subprocess stub whose wait() can block on a threading.Event until explicit release."""

    def __init__(
        self,
        pid: int = 100,
        *,
        wait_entered: Optional[threading.Event] = None,
        release_wait: Optional[threading.Event] = None,
        wait_exited: Optional[threading.Event] = None,
    ) -> None:
        self.pid = pid
        self._returncode: Optional[int] = None
        self.terminated = False
        self.killed = False
        self._wait_entered = wait_entered
        self._release_wait = release_wait
        self._wait_exited = wait_exited

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
        try:
            if self._release_wait is not None:
                self._release_wait.wait()
            if self._returncode is None:
                self._returncode = -15 if self.terminated else 0
            return self._returncode
        finally:
            if self._wait_exited is not None:
                self._wait_exited.set()


class _TrackedCycleLock:
    """Wraps a threading.Lock to provide deterministic contender/acquisition events."""

    def __init__(self, underlying: threading.Lock) -> None:
        self._underlying = underlying
        self._meta_lock = threading.Lock()
        self._acquire_count = 0
        self.contender_attempting = threading.Event()
        self.contender_acquired = threading.Event()

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        with self._meta_lock:
            self._acquire_count += 1
            call_num = self._acquire_count
        if call_num == 2:
            self.contender_attempting.set()
        acquired = (
            self._underlying.acquire(blocking, timeout)
            if timeout != -1
            else self._underlying.acquire(blocking)
        )
        if call_num == 2 and acquired:
            self.contender_acquired.set()
        return acquired

    def release(self) -> None:
        self._underlying.release()

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.release()


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


def _start_capturing_thread(
    fn: Callable[[], Any],
    errors: List[BaseException],
) -> threading.Thread:
    def _runner() -> None:
        try:
            fn()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    return thread


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

    def _run_bounded_reader_while_writer_blocked(
        self,
        *,
        cycle_thread: threading.Thread,
        entered_event: threading.Event,
        release_event: threading.Event,
        exited_event: threading.Event,
        reader_fn: Callable[[], None],
    ) -> None:
        """Wait for writer to enter blocked I/O, run reader in a bounded thread,
        and assert the writer remains blocked when the reader finishes."""
        self.assertTrue(entered_event.wait(timeout=5))
        self.assertFalse(release_event.is_set())
        self.assertFalse(exited_event.is_set())
        self.assertTrue(cycle_thread.is_alive())

        reader_errors: List[BaseException] = []
        reader_thread = _start_capturing_thread(reader_fn, reader_errors)
        self.addCleanup(reader_thread.join, timeout=5)
        reader_thread.join(timeout=5)
        self.assertFalse(
            reader_thread.is_alive(),
            "state reader thread blocked while reconcile cycle was waiting on I/O",
        )
        if reader_errors:
            raise reader_errors[0]

        # Writer must still be blocked inside the mocked I/O when readers finish.
        self.assertFalse(release_event.is_set())
        self.assertFalse(exited_event.is_set())
        self.assertTrue(cycle_thread.is_alive())

    def test_state_reads_return_while_cycle_blocked_in_source_snapshot_fetch(self) -> None:
        store = RuntimeBindingStore()
        store.create(_make_binding_record("b-fetch-001", runtime_id="rt-fetch-001"), single_runtime_enforced=False)

        fetch_entered = threading.Event()
        release_fetch = threading.Event()
        fetch_exited = threading.Event()
        block_next_fetch = False

        class _Reconciler(PaperFleetReconciler):
            def _spawn(inner_self, binding_id: str, port: int, env: Any) -> _EventProcess:
                return _EventProcess(pid=201)

            def _fetch_source_snapshot(inner_self, symbol: str) -> Optional[Dict[str, Any]]:
                if symbol == "MSFT.US" and block_next_fetch:
                    fetch_entered.set()
                    try:
                        release_fetch.wait()
                    finally:
                        fetch_exited.set()
                return _fresh_snapshot(symbol=symbol, snapshot_id=f"snap-{symbol}")

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

        read_results: Dict[str, Any] = {}
        cycle_errors: List[BaseException] = []
        cycle_thread = _start_capturing_thread(recon.reconcile_once, cycle_errors)
        try:
            def _reader() -> None:
                read_results["snapshot"] = recon.snapshot()
                read_results["fleet_state"] = self._http_get_json(recon, "/api/fleet/state")
                read_results["readyz"] = self._http_get_json(recon, "/readyz")
                read_results["healthz"] = self._http_get_json(recon, "/healthz")
                read_results["before_release"] = not release_fetch.is_set() and not fetch_exited.is_set()

            self._run_bounded_reader_while_writer_blocked(
                cycle_thread=cycle_thread,
                entered_event=fetch_entered,
                release_event=release_fetch,
                exited_event=fetch_exited,
                reader_fn=_reader,
            )
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
        finally:
            release_fetch.set()
            cycle_thread.join(timeout=5)

        self.assertFalse(cycle_thread.is_alive())
        if cycle_errors:
            raise cycle_errors[0]

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
        transition_exited = threading.Event()
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
                    try:
                        release_transition.wait()
                    finally:
                        transition_exited.set()
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
        read_results: Dict[str, Any] = {}
        cycle_errors: List[BaseException] = []
        cycle_thread = _start_capturing_thread(recon.reconcile_once, cycle_errors)
        try:
            def _reader() -> None:
                read_results["snapshot"] = recon.snapshot()
                read_results["fleet_state"] = self._http_get_json(recon, "/api/fleet/state")
                read_results["readyz"] = self._http_get_json(recon, "/readyz")
                read_results["before_release"] = (
                    not release_transition.is_set() and not transition_exited.is_set()
                )

            self._run_bounded_reader_while_writer_blocked(
                cycle_thread=cycle_thread,
                entered_event=transition_entered,
                release_event=release_transition,
                exited_event=transition_exited,
                reader_fn=_reader,
            )
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
        finally:
            release_transition.set()
            cycle_thread.join(timeout=5)

        self.assertFalse(cycle_thread.is_alive())
        if cycle_errors:
            raise cycle_errors[0]

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
        spawn_exited = threading.Event()
        wait_entered = threading.Event()
        release_wait = threading.Event()
        wait_exited = threading.Event()
        loop_write_entered = threading.Event()
        release_loop_write = threading.Event()
        loop_write_exited = threading.Event()

        block_spawn = False
        block_loop_write = False

        class _BlockingLoopWriter:
            def __init__(self) -> None:
                self.calls: List[Dict[str, Any]] = []

            async def record_success(self, **kwargs: Any) -> None:
                if block_loop_write:
                    loop_write_entered.set()
                    try:
                        release_loop_write.wait()
                    finally:
                        loop_write_exited.set()
                self.calls.append(kwargs)

            async def record_failure(self, **kwargs: Any) -> None:
                self.calls.append(kwargs)

        writer = _BlockingLoopWriter()

        class _Reconciler(PaperFleetReconciler):
            def _spawn(inner_self, binding_id: str, port: int, env: Any) -> _EventProcess:
                if block_spawn:
                    spawn_entered.set()
                    try:
                        release_spawn.wait()
                    finally:
                        spawn_exited.set()
                return _EventProcess(
                    pid=401,
                    wait_entered=wait_entered,
                    release_wait=release_wait,
                    wait_exited=wait_exited,
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
        spawn_reads: Dict[str, Any] = {}
        t1_errors: List[BaseException] = []
        t1 = _start_capturing_thread(recon.reconcile_once, t1_errors)
        try:
            def _read_during_spawn() -> None:
                spawn_reads["snapshot"] = recon.snapshot()
                spawn_reads["fleet_state"] = self._http_get_json(recon, "/api/fleet/state")

            self._run_bounded_reader_while_writer_blocked(
                cycle_thread=t1,
                entered_event=spawn_entered,
                release_event=release_spawn,
                exited_event=spawn_exited,
                reader_fn=_read_during_spawn,
            )
            # Reader sees 0 workers and 1 opened monitoring session while _spawn is blocked.
            snap_during_spawn = spawn_reads["snapshot"]
            self.assertEqual(snap_during_spawn["worker_count"], 0)
            self.assertEqual(snap_during_spawn["active_monitoring_session_count"], 1)
            self._assert_consistent_worker_session_view(snap_during_spawn)
            code_spawn, body_spawn = spawn_reads["fleet_state"]
            self.assertEqual(code_spawn, 200)
            self._assert_consistent_worker_session_view(body_spawn)
        finally:
            release_spawn.set()
            t1.join(timeout=5)

        self.assertFalse(t1.is_alive())
        if t1_errors:
            raise t1_errors[0]
        block_spawn = False

        snap_after_spawn = recon.snapshot()
        self.assertEqual(snap_after_spawn["worker_count"], 1)
        self.assertEqual(snap_after_spawn["active_monitoring_session_count"], 1)
        self._assert_consistent_worker_session_view(snap_after_spawn)
        self.assertEqual(len(writer.calls), 1)

        # 2. Block inside loop-truth publish during cycle 2.
        block_loop_write = True
        loop_reads: Dict[str, Any] = {}
        t2_errors: List[BaseException] = []
        t2 = _start_capturing_thread(recon.reconcile_once, t2_errors)
        try:
            def _read_during_loop_write() -> None:
                loop_reads["snapshot"] = recon.snapshot()
                loop_reads["fleet_state"] = self._http_get_json(recon, "/api/fleet/state")
                loop_reads["readyz"] = self._http_get_json(recon, "/readyz")

            self._run_bounded_reader_while_writer_blocked(
                cycle_thread=t2,
                entered_event=loop_write_entered,
                release_event=release_loop_write,
                exited_event=loop_write_exited,
                reader_fn=_read_during_loop_write,
            )
            self.assertEqual(len(writer.calls), 1)
            code_during_loop, body_during_loop = loop_reads["fleet_state"]
            self.assertEqual(code_during_loop, 200)
            self._assert_consistent_worker_session_view(body_during_loop)
            ready_code_loop, ready_body_loop = loop_reads["readyz"]
            self.assertEqual(ready_code_loop, 200)
            self.assertTrue(ready_body_loop["ready"])
        finally:
            release_loop_write.set()
            t2.join(timeout=5)

        self.assertFalse(t2.is_alive())
        if t2_errors:
            raise t2_errors[0]
        block_loop_write = False
        self.assertEqual(len(writer.calls), 2)
        self.assertEqual(recon.snapshot()["cycle_count"], 2)

        # 3. Retire binding so cycle 3 blocks inside process.wait() during _terminate_worker.
        store.transition_status("b-proc-001", "retired")
        term_reads: Dict[str, Any] = {}
        t3_errors: List[BaseException] = []
        t3 = _start_capturing_thread(recon.reconcile_once, t3_errors)
        try:
            def _read_during_terminate() -> None:
                term_reads["snapshot"] = recon.snapshot()
                term_reads["fleet_state"] = self._http_get_json(recon, "/api/fleet/state")

            self._run_bounded_reader_while_writer_blocked(
                cycle_thread=t3,
                entered_event=wait_entered,
                release_event=release_wait,
                exited_event=wait_exited,
                reader_fn=_read_during_terminate,
            )
            snap_during_terminate = term_reads["snapshot"]
            self.assertEqual(snap_during_terminate["worker_count"], 1)
            self.assertEqual(snap_during_terminate["active_monitoring_session_count"], 1)
            self._assert_consistent_worker_session_view(snap_during_terminate)
            code_term, body_term = term_reads["fleet_state"]
            self.assertEqual(code_term, 200)
            self._assert_consistent_worker_session_view(body_term)
        finally:
            release_wait.set()
            t3.join(timeout=5)

        self.assertFalse(t3.is_alive())
        if t3_errors:
            raise t3_errors[0]

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
        cycle1_fetch_exited = threading.Event()
        cycle2_entered_fetch_fleet = threading.Event()
        spawned: List[Dict[str, Any]] = []
        fetch_calls = 0
        active_cycles = 0
        max_concurrent_cycles = 0
        active_holder: Optional[threading.Thread] = None
        events: List[str] = []
        counter_lock = threading.Lock()

        class _Reconciler(PaperFleetReconciler):
            def _fetch_fleet_state(inner_self):
                nonlocal fetch_calls, active_cycles, max_concurrent_cycles, active_holder
                with counter_lock:
                    fetch_calls += 1
                    call_num = fetch_calls
                    active_cycles += 1
                    if active_cycles > max_concurrent_cycles:
                        max_concurrent_cycles = active_cycles
                    active_holder = threading.current_thread()
                    events.append(f"cycle{call_num}_enter_serialized")
                if call_num == 2:
                    cycle2_entered_fetch_fleet.set()
                return super()._fetch_fleet_state()

            def _fetch_source_snapshot(inner_self, symbol: str) -> Optional[Dict[str, Any]]:
                with counter_lock:
                    self.assertEqual(active_cycles, 1)
                    self.assertIs(active_holder, threading.current_thread())
                if not cycle1_in_fetch.is_set():
                    cycle1_in_fetch.set()
                    try:
                        release_cycle1.wait()
                    finally:
                        cycle1_fetch_exited.set()
                return _fresh_snapshot(symbol=symbol)

            def _spawn(inner_self, binding_id: str, port: int, env: Any) -> _EventProcess:
                with counter_lock:
                    self.assertEqual(active_cycles, 1)
                    self.assertIs(active_holder, threading.current_thread())
                proc = _EventProcess(pid=500 + len(spawned))
                spawned.append({"binding_id": binding_id, "port": port, "proc": proc})
                return proc

            def _publish_loop_truth(
                inner_self,
                desired: Optional[Dict[str, Dict[str, Any]]],
                *,
                failure_reason: Optional[str] = None,
            ) -> None:
                nonlocal active_cycles, active_holder
                try:
                    super()._publish_loop_truth(desired, failure_reason=failure_reason)
                finally:
                    with counter_lock:
                        self.assertEqual(active_cycles, 1)
                        self.assertIs(active_holder, threading.current_thread())
                        events.append(f"cycle{fetch_calls}_exit_serialized")
                        active_cycles -= 1
                        active_holder = None

        recon = _Reconciler(
            store=store,
            source_ingest_url="http://source-ingest:8097",
            leader_store=InMemoryFencedLeaderStore(),
            poll_interval_seconds=999,
            worker_base_port=9400,
            market_input_bootstrap_grace_seconds=0.0,
        )
        tracked_cycle_lock = _TrackedCycleLock(recon._cycle_lock)
        recon._cycle_lock = tracked_cycle_lock  # type: ignore[assignment]

        t1_errors: List[BaseException] = []
        t2_errors: List[BaseException] = []
        t1 = _start_capturing_thread(recon.reconcile_once, t1_errors)
        t2: Optional[threading.Thread] = None
        try:
            self.assertTrue(cycle1_in_fetch.wait(timeout=5))

            t2 = _start_capturing_thread(recon.reconcile_once, t2_errors)

            # Explicit contender/acquisition handshake: wait until Cycle 2 reaches
            # _cycle_lock.acquire(), then verify it has not acquired the lock or
            # entered _fetch_fleet_state while Cycle 1 is blocked in _fetch_source_snapshot.
            self.assertTrue(tracked_cycle_lock.contender_attempting.wait(timeout=5))
            self.assertFalse(tracked_cycle_lock.contender_acquired.is_set())
            self.assertFalse(cycle2_entered_fetch_fleet.is_set())

            # Meanwhile state reads in a bounded reader thread complete without
            # waiting for either cycle while Cycle 1 remains blocked and Cycle 2
            # remains queued on _cycle_lock.
            read_results: Dict[str, Any] = {}

            def _reader() -> None:
                read_results["snapshot"] = recon.snapshot()
                read_results["fleet_state"] = self._http_get_json(recon, "/api/fleet/state")

            self._run_bounded_reader_while_writer_blocked(
                cycle_thread=t1,
                entered_event=cycle1_in_fetch,
                release_event=release_cycle1,
                exited_event=cycle1_fetch_exited,
                reader_fn=_reader,
            )
            self.assertFalse(tracked_cycle_lock.contender_acquired.is_set())
            self.assertFalse(cycle2_entered_fetch_fleet.is_set())
            self.assertTrue(t2.is_alive())

            code, body = read_results["fleet_state"]
            self.assertEqual(code, 200)
            self.assertEqual(body["cycle_count"], 0)
            self.assertEqual(read_results["snapshot"]["cycle_count"], 0)
        finally:
            release_cycle1.set()
            t1.join(timeout=5)
            if t2 is not None:
                t2.join(timeout=5)

        self.assertFalse(t1.is_alive())
        self.assertIsNotNone(t2)
        assert t2 is not None
        self.assertFalse(t2.is_alive())
        if t1_errors:
            raise t1_errors[0]
        if t2_errors:
            raise t2_errors[0]

        self.assertTrue(tracked_cycle_lock.contender_acquired.is_set())
        self.assertTrue(cycle2_entered_fetch_fleet.is_set())
        self.assertEqual(active_cycles, 0)
        self.assertEqual(max_concurrent_cycles, 1)
        self.assertEqual(
            events,
            [
                "cycle1_enter_serialized",
                "cycle1_exit_serialized",
                "cycle2_enter_serialized",
                "cycle2_exit_serialized",
            ],
        )
        self.assertEqual(len(spawned), 1)
        final_snap = recon.snapshot()
        self.assertEqual(final_snap["cycle_count"], 2)
        self.assertEqual(final_snap["worker_count"], 1)
        self.assertEqual(final_snap["running_count"], 1)
        self._assert_consistent_worker_session_view(final_snap)

    def test_terminate_worker_defers_session_end_publication_until_worker_removed(self) -> None:
        store = RuntimeBindingStore()
        store.create(
            _make_binding_record("b-term-pub-001", runtime_id="rt-term-pub-001"),
            single_runtime_enforced=False,
        )

        end_session_entered = threading.Event()
        release_end_session = threading.Event()
        end_session_exited = threading.Event()
        block_on_session_end = False
        boundary_state: Dict[str, Any] = {}
        published_generations: List[Dict[str, Any]] = []

        class _Reconciler(PaperFleetReconciler):
            def _spawn(inner_self, binding_id: str, port: int, env: Any) -> _EventProcess:
                return _EventProcess(pid=601)

            def _fetch_source_snapshot(inner_self, symbol: str) -> Optional[Dict[str, Any]]:
                return _fresh_snapshot(symbol=symbol)

            def _publish_snapshot(inner_self) -> None:
                super()._publish_snapshot()
                if getattr(inner_self, "_defer_snapshot_publish_depth", 0) == 0:
                    published_generations.append(inner_self.snapshot())

            def _end_monitoring_session(
                inner_self,
                session_id: Optional[str],
                **kwargs: Any,
            ) -> None:
                super()._end_monitoring_session(session_id, **kwargs)
                if block_on_session_end and session_id:
                    entry = inner_self._workers.get("b-term-pub-001")
                    session = inner_self._monitoring_sessions.get(session_id, {})
                    boundary_state["worker_still_present"] = entry is not None
                    boundary_state["worker_private_status"] = (
                        entry.status if entry is not None else None
                    )
                    boundary_state["session_private_status"] = session.get("status")
                    end_session_entered.set()
                    try:
                        release_end_session.wait()
                    finally:
                        end_session_exited.set()

        recon = _Reconciler(
            store=store,
            source_ingest_url="http://source-ingest:8097",
            leader_store=InMemoryFencedLeaderStore(),
            poll_interval_seconds=999,
            worker_base_port=9500,
            market_input_bootstrap_grace_seconds=0.0,
        )

        first = recon.reconcile_once()
        self.assertEqual(first["cycle_count"], 1)
        self.assertEqual(first["worker_count"], 1)
        self.assertEqual(first["running_count"], 1)
        self.assertEqual(first["active_monitoring_session_count"], 1)
        self._assert_consistent_worker_session_view(first)

        # Retire the binding and pause inside _terminate_worker immediately after
        # _end_monitoring_session mutates the session and before worker removal.
        store.transition_status("b-term-pub-001", "retired")
        block_on_session_end = True
        read_results: Dict[str, Any] = {}
        cycle_errors: List[BaseException] = []
        cycle_thread = _start_capturing_thread(recon.reconcile_once, cycle_errors)
        try:
            def _reader() -> None:
                read_results["snapshot"] = recon.snapshot()
                read_results["fleet_state"] = self._http_get_json(recon, "/api/fleet/state")
                read_results["readyz"] = self._http_get_json(recon, "/readyz")
                read_results["healthz"] = self._http_get_json(recon, "/healthz")
                read_results["before_release"] = (
                    not release_end_session.is_set() and not end_session_exited.is_set()
                )

            self._run_bounded_reader_while_writer_blocked(
                cycle_thread=cycle_thread,
                entered_event=end_session_entered,
                release_event=release_end_session,
                exited_event=end_session_exited,
                reader_fn=_reader,
            )
            self.assertTrue(read_results.get("before_release"))
            self.assertTrue(boundary_state.get("worker_still_present"))
            self.assertEqual(boundary_state.get("worker_private_status"), "running")
            self.assertEqual(boundary_state.get("session_private_status"), "ended")

            snap_during = read_results["snapshot"]
            self.assertEqual(snap_during["worker_count"], 1)
            self.assertEqual(snap_during["running_count"], 1)
            self.assertEqual(snap_during["active_monitoring_session_count"], 1)
            self.assertEqual(snap_during["ended_monitoring_session_count"], 0)
            self._assert_consistent_worker_session_view(snap_during)

            code_during, body_during = read_results["fleet_state"]
            self.assertEqual(code_during, 200)
            self.assertEqual(body_during["worker_count"], 1)
            self.assertEqual(body_during["running_count"], 1)
            self.assertEqual(body_during["active_monitoring_session_count"], 1)
            self._assert_consistent_worker_session_view(body_during)

            ready_code, ready_body = read_results["readyz"]
            self.assertEqual(ready_code, 200)
            self.assertTrue(ready_body["ready"])
            health_code, health_body = read_results["healthz"]
            self.assertEqual(health_code, 200)
            self.assertTrue(health_body["ready"])
        finally:
            release_end_session.set()
            cycle_thread.join(timeout=5)

        self.assertFalse(cycle_thread.is_alive())
        if cycle_errors:
            raise cycle_errors[0]

        snap_after = recon.snapshot()
        self.assertEqual(snap_after["cycle_count"], 2)
        self.assertEqual(snap_after["worker_count"], 0)
        self.assertEqual(snap_after["running_count"], 0)
        self.assertEqual(snap_after["active_monitoring_session_count"], 0)
        self.assertEqual(snap_after["ended_monitoring_session_count"], 1)
        self._assert_consistent_worker_session_view(snap_after)

        code_after, body_after = self._http_get_json(recon, "/api/fleet/state")
        self.assertEqual(code_after, 200)
        self.assertEqual(body_after["worker_count"], 0)
        self.assertEqual(body_after["running_count"], 0)
        self.assertEqual(body_after["active_monitoring_session_count"], 0)
        self.assertEqual(body_after["ended_monitoring_session_count"], 1)
        self._assert_consistent_worker_session_view(body_after)

        for gen in published_generations:
            self._assert_consistent_worker_session_view(gen)

    def test_stale_session_closure_defers_publication_until_worker_removed(self) -> None:
        store = RuntimeBindingStore()
        store.create(
            _make_binding_record("b-stale-pub-001", runtime_id="rt-stale-pub-001"),
            single_runtime_enforced=False,
        )

        stale_end_entered = threading.Event()
        release_stale_end = threading.Event()
        stale_end_exited = threading.Event()
        block_on_stale_end = False
        heartbeat_iso = [
            datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        ]
        boundary_state: Dict[str, Any] = {}
        published_generations: List[Dict[str, Any]] = []
        spawn_count = 0

        class _Reconciler(PaperFleetReconciler):
            def _spawn(inner_self, binding_id: str, port: int, env: Any) -> _EventProcess:
                nonlocal spawn_count
                spawn_count += 1
                return _EventProcess(pid=700 + spawn_count)

            def _fetch_source_snapshot(inner_self, symbol: str) -> Optional[Dict[str, Any]]:
                return _fresh_snapshot(symbol=symbol)

            def _fetch_runtime_summaries(inner_self) -> Optional[Dict[str, Dict[str, Any]]]:
                return {
                    "rt-stale-pub-001": {
                        "runtime_id": "rt-stale-pub-001",
                        "last_heartbeat_at": heartbeat_iso[0],
                        "state": "active",
                    }
                }

            def _publish_snapshot(inner_self) -> None:
                super()._publish_snapshot()
                if getattr(inner_self, "_defer_snapshot_publish_depth", 0) == 0:
                    published_generations.append(inner_self.snapshot())

            def _end_monitoring_session(
                inner_self,
                session_id: Optional[str],
                **kwargs: Any,
            ) -> None:
                super()._end_monitoring_session(session_id, **kwargs)
                if (
                    block_on_stale_end
                    and session_id
                    and kwargs.get("staleness") is not None
                    and not stale_end_entered.is_set()
                ):
                    entry = inner_self._workers.get("b-stale-pub-001")
                    session = inner_self._monitoring_sessions.get(session_id, {})
                    boundary_state["worker_still_present"] = entry is not None
                    boundary_state["worker_private_status"] = (
                        entry.status if entry is not None else None
                    )
                    boundary_state["session_private_status"] = session.get("status")
                    boundary_state["session_staleness_reason"] = (
                        (session.get("staleness") or {}).get("reason")
                    )
                    stale_end_entered.set()
                    try:
                        release_stale_end.wait()
                    finally:
                        stale_end_exited.set()

        recon = _Reconciler(
            store=store,
            source_ingest_url="http://source-ingest:8097",
            telemetry_api_url="http://telemetry:8095",
            monitoring_heartbeat_stale_after_seconds=30,
            leader_store=InMemoryFencedLeaderStore(),
            poll_interval_seconds=999,
            worker_base_port=9600,
            market_input_bootstrap_grace_seconds=0.0,
        )

        first = recon.reconcile_once()
        self.assertEqual(first["cycle_count"], 1)
        self.assertEqual(first["worker_count"], 1)
        self.assertEqual(first["running_count"], 1)
        self.assertEqual(first["active_monitoring_session_count"], 1)
        first_session_id = first["workers"][0]["monitoring_session_id"]
        self._assert_consistent_worker_session_view(first)

        # Report a stale heartbeat in cycle 2 so _reap_stale_monitoring_sessions
        # queues a pending stale-session closure and calls _terminate_worker.
        heartbeat_iso[0] = (
            (datetime.now(timezone.utc) - timedelta(seconds=300))
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        block_on_stale_end = True
        read_results: Dict[str, Any] = {}
        cycle_errors: List[BaseException] = []
        cycle_thread = _start_capturing_thread(recon.reconcile_once, cycle_errors)
        try:
            def _reader() -> None:
                read_results["snapshot"] = recon.snapshot()
                read_results["fleet_state"] = self._http_get_json(recon, "/api/fleet/state")
                read_results["readyz"] = self._http_get_json(recon, "/readyz")
                read_results["before_release"] = (
                    not release_stale_end.is_set() and not stale_end_exited.is_set()
                )

            self._run_bounded_reader_while_writer_blocked(
                cycle_thread=cycle_thread,
                entered_event=stale_end_entered,
                release_event=release_stale_end,
                exited_event=stale_end_exited,
                reader_fn=_reader,
            )
            self.assertTrue(read_results.get("before_release"))
            self.assertTrue(boundary_state.get("worker_still_present"))
            self.assertEqual(boundary_state.get("worker_private_status"), "running")
            self.assertEqual(boundary_state.get("session_private_status"), "ended")
            self.assertEqual(boundary_state.get("session_staleness_reason"), "stale_heartbeat")

            snap_during = read_results["snapshot"]
            self.assertEqual(snap_during["worker_count"], 1)
            self.assertEqual(snap_during["running_count"], 1)
            self.assertEqual(snap_during["active_monitoring_session_count"], 1)
            self._assert_consistent_worker_session_view(snap_during)

            code_during, body_during = read_results["fleet_state"]
            self.assertEqual(code_during, 200)
            self.assertEqual(body_during["worker_count"], 1)
            self.assertEqual(body_during["running_count"], 1)
            self.assertEqual(body_during["active_monitoring_session_count"], 1)
            self._assert_consistent_worker_session_view(body_during)

            ready_code, ready_body = read_results["readyz"]
            self.assertEqual(ready_code, 200)
            self.assertTrue(ready_body["ready"])
        finally:
            release_stale_end.set()
            cycle_thread.join(timeout=5)

        self.assertFalse(cycle_thread.is_alive())
        if cycle_errors:
            raise cycle_errors[0]

        snap_after = recon.snapshot()
        self.assertEqual(snap_after["cycle_count"], 2)
        self.assertEqual(snap_after["worker_count"], 1)
        self.assertEqual(snap_after["running_count"], 1)
        self.assertEqual(snap_after["active_monitoring_session_count"], 1)
        self.assertEqual(snap_after["ended_monitoring_session_count"], 1)
        self._assert_consistent_worker_session_view(snap_after)

        sessions_by_id = {s["session_id"]: s for s in snap_after["monitoring_sessions"]}
        self.assertIn(first_session_id, sessions_by_id)
        self.assertFalse(sessions_by_id[first_session_id]["active"])
        self.assertEqual(sessions_by_id[first_session_id]["ended_reason"], "stale_heartbeat")
        self.assertEqual(
            sessions_by_id[first_session_id]["staleness"]["reason"],
            "stale_heartbeat",
        )

        code_after, body_after = self._http_get_json(recon, "/api/fleet/state")
        self.assertEqual(code_after, 200)
        self._assert_consistent_worker_session_view(body_after)

        for gen in published_generations:
            self._assert_consistent_worker_session_view(gen)


if __name__ == "__main__":
    unittest.main()

"""
Paper Runtime Fleet Reconciler

Polls the runtime-manager for active paper RuntimeBindings and maintains
exactly one worker subprocess per binding. Replaces manual docker run.

Reconcile loop (default every 15 s):
  - fetch all bindings from runtime-manager
  - desired = {binding_id: binding  for each active paper binding}
  - for each desired binding missing a live worker  → start subprocess
  - for each tracked worker whose binding is no longer active → terminate
  - for each tracked worker whose process has exited  → restart (up to cap)

HTTP surface (for health checks and state inspection):
  GET /healthz  → 200 if reconciler loop is alive, else 503
  GET /livez    → always 200 (liveness only)
  GET /readyz   → 200 when at least one reconcile cycle has completed
  GET /api/fleet/state  → full reconciler snapshot

Environment variables consumed by the reconciler:
  PANTHEON_RUNTIME_MANAGER_URL      runtime-manager HTTP base URL
  PANTHEON_RUNTIME_MANAGER_TOKEN    bearer token
  RECONCILER_POLL_INTERVAL_SECONDS  reconcile period (default 15)
  RECONCILER_WORKER_BASE_PORT       first port for spawned workers (default 8020)
  RECONCILER_MAX_RESTARTS           per-binding restart cap (default 5)
  RECONCILER_RESTART_BACKOFF_SECONDS base backoff per restart (default 5)
  RECONCILER_DRAIN_TIMEOUT_SECONDS  SIGTERM→SIGKILL timeout (default 10)
  PANTHEON_TELEMETRY_URL            forwarded to each worker
  SIGNAL_STORE_URL                  forwarded to each worker
  WORKER_SCRIPT_PATH                override path to paper_runtime.py
  HOST / PORT                       reconciler HTTP interface (default 0.0.0.0/8011)
"""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[2]
_DEFAULT_WORKER_SCRIPT = str(
    _HERE.parent / "lean_runtime" / "paper_runtime.py"
)

log = logging.getLogger(__name__)


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _as_float(value: str | None, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _as_int(value: str | None, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Worker entry
# ---------------------------------------------------------------------------

@dataclass
class WorkerEntry:
    binding_id: str
    runtime_id: str
    capital_pool_id: str
    port: int
    process: Any  # subprocess.Popen | None
    started_at: str
    restart_count: int = 0
    last_exit_code: Optional[int] = None
    last_error: Optional[str] = None
    status: str = "running"  # running | dead | restarting | stopped
    dead_at: Optional[float] = None  # time.monotonic() when process last exited


# ---------------------------------------------------------------------------
# Reconciler
# ---------------------------------------------------------------------------

class PaperFleetReconciler:
    """Maintain one worker subprocess per active paper RuntimeBinding."""

    def __init__(
        self,
        *,
        runtime_manager_url: Optional[str] = None,
        runtime_manager_token: Optional[str] = None,
        poll_interval_seconds: Optional[float] = None,
        worker_base_port: Optional[int] = None,
        max_restarts: Optional[int] = None,
        restart_backoff_seconds: Optional[float] = None,
        drain_timeout_seconds: Optional[float] = None,
        worker_script_path: Optional[str] = None,
        extra_env: Optional[Dict[str, str]] = None,
    ) -> None:
        self._url = (
            runtime_manager_url
            or os.getenv("PANTHEON_RUNTIME_MANAGER_URL", "")
        ).rstrip("/")
        self._token = runtime_manager_token or os.getenv(
            "PANTHEON_RUNTIME_MANAGER_TOKEN", ""
        )
        self._poll_interval = poll_interval_seconds or _as_float(
            os.getenv("RECONCILER_POLL_INTERVAL_SECONDS"), 15.0
        )
        self._worker_base_port = worker_base_port or _as_int(
            os.getenv("RECONCILER_WORKER_BASE_PORT"), 8020
        )
        self._max_restarts = max_restarts if max_restarts is not None else _as_int(
            os.getenv("RECONCILER_MAX_RESTARTS"), 5
        )
        self._restart_backoff = restart_backoff_seconds or _as_float(
            os.getenv("RECONCILER_RESTART_BACKOFF_SECONDS"), 5.0
        )
        self._drain_timeout = drain_timeout_seconds or _as_float(
            os.getenv("RECONCILER_DRAIN_TIMEOUT_SECONDS"), 10.0
        )
        self._worker_script = worker_script_path or os.getenv(
            "WORKER_SCRIPT_PATH", _DEFAULT_WORKER_SCRIPT
        )
        self._extra_env: Dict[str, str] = dict(extra_env or {})

        self._lock = threading.RLock()
        self._workers: Dict[str, WorkerEntry] = {}
        self._used_ports: set[int] = set()
        self._reconcile_thread: Optional[threading.Thread] = None
        self._shutdown = threading.Event()
        self._started_at = _iso_now()
        self._cycle_count = 0
        self._last_reconcile_at: Optional[str] = None
        self._last_error: Optional[str] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._reconcile_thread is not None:
            return
        self._reconcile_thread = threading.Thread(
            target=self._run_loop, daemon=True, name="fleet-reconciler-loop"
        )
        self._reconcile_thread.start()
        log.info("fleet reconciler started; poll_interval=%.1fs", self._poll_interval)

    def stop(self) -> None:
        log.info("fleet reconciler stopping")
        self._shutdown.set()
        if self._reconcile_thread is not None:
            self._reconcile_thread.join(timeout=max(self._drain_timeout, 5.0))
        with self._lock:
            for binding_id in list(self._workers):
                self._terminate_worker(binding_id, reason="reconciler shutdown")

    # ------------------------------------------------------------------
    # Reconcile
    # ------------------------------------------------------------------

    def reconcile_once(self) -> Dict[str, Any]:
        """Run one reconcile cycle. Returns a snapshot of the fleet state."""
        bindings = self._fetch_active_paper_bindings()
        # bindings is None when the fetch failed — we must not evict existing workers
        # in that case, since we have no reliable picture of desired state.

        with self._lock:
            # Poll live processes for exit — always run, even on fetch failure
            for binding_id, entry in list(self._workers.items()):
                if entry.process is None or entry.status in ("stopped", "dead"):
                    continue
                rc = entry.process.poll()
                if rc is not None:
                    entry.last_exit_code = rc
                    entry.status = "dead"
                    entry.dead_at = time.monotonic()
                    # Free the port so the restart can allocate a clean one
                    self._free_port(entry.port)
                    entry.port = 0
                    log.warning(
                        "worker for binding %s exited with code %d (restarts=%d)",
                        binding_id,
                        rc,
                        entry.restart_count,
                    )

            if bindings is not None:
                desired: Dict[str, Dict[str, Any]] = {
                    b["binding_id"]: b for b in bindings
                }

                # Start workers for new or dead-but-desired active bindings
                for binding_id, binding in desired.items():
                    if binding_id not in self._workers:
                        self._start_worker(binding)
                    elif self._workers[binding_id].status == "dead":
                        entry = self._workers[binding_id]
                        if entry.restart_count < self._max_restarts:
                            backoff = entry.restart_count * self._restart_backoff
                            ready = (
                                entry.dead_at is None
                                or time.monotonic() >= entry.dead_at + backoff
                            )
                            if ready:
                                self._start_worker(binding, restart_count=entry.restart_count + 1)
                        else:
                            log.warning(
                                "binding %s: restart cap reached (%d), not restarting",
                                binding_id,
                                self._max_restarts,
                            )

                # Stop workers for bindings that are no longer active
                for binding_id in list(self._workers):
                    if binding_id not in desired:
                        self._terminate_worker(binding_id, reason="binding no longer active")

                self._last_error = None

            self._cycle_count += 1
            self._last_reconcile_at = _iso_now()
            return self._snapshot()

    def _run_loop(self) -> None:
        while not self._shutdown.is_set():
            try:
                self.reconcile_once()
            except Exception as exc:  # noqa: BLE001
                with self._lock:
                    self._last_error = f"{type(exc).__name__}: {exc}"
                log.exception("fleet reconciler cycle failed")
            self._shutdown.wait(self._poll_interval)

    # ------------------------------------------------------------------
    # Worker management
    # ------------------------------------------------------------------

    def _allocate_port(self) -> int:
        port = self._worker_base_port
        while port in self._used_ports:
            port += 1
        self._used_ports.add(port)
        return port

    def _free_port(self, port: int) -> None:
        self._used_ports.discard(port)

    def _build_worker_env(self, binding: Dict[str, Any]) -> Dict[str, str]:
        env = dict(os.environ)
        env.update(self._extra_env)
        env.update(
            {
                "PANTHEON_RUNTIME_BINDING_ID": str(binding.get("binding_id") or ""),
                "PANTHEON_RUNTIME_ID": str(binding.get("runtime_id") or ""),
                "PANTHEON_DEPLOYMENT_PLAN_ID": str(binding.get("plan_id") or ""),
                "PANTHEON_DEPLOYMENT_STAGE": "paper",
                "PANTHEON_RUNTIME_MODE": "paper",
                "PANTHEON_ARTIFACT_ID": str(binding.get("artifact_id") or ""),
                "PANTHEON_ARTIFACT_VERSION": str(binding.get("artifact_version") or ""),
                "PANTHEON_CAPITAL_POOL_ID": str(binding.get("capital_pool_id") or ""),
                "PANTHEON_PERSONA_CAPITAL_BINDING_ID": str(
                    binding.get("persona_capital_binding_id") or ""
                ),
            }
        )
        if self._url:
            env["PANTHEON_RUNTIME_MANAGER_URL"] = self._url
        if self._token:
            env["PANTHEON_RUNTIME_MANAGER_TOKEN"] = self._token
        telemetry_url = os.getenv("PANTHEON_TELEMETRY_URL", "")
        if telemetry_url:
            env["PANTHEON_TELEMETRY_URL"] = telemetry_url
        signal_store_url = os.getenv("SIGNAL_STORE_URL", "redis://signal-store:6379")
        env["SIGNAL_STORE_URL"] = signal_store_url
        # Scope the Redis queue key to this binding so workers cannot consume
        # signals belonging to a different binding via the shared default key.
        binding_id = str(binding.get("binding_id") or "")
        if binding_id:
            env["PANTHEON_SIGNAL_QUEUE_KEY"] = f"pantheon:signals:pending:{binding_id}"
        return env

    def _start_worker(
        self, binding: Dict[str, Any], restart_count: int = 0
    ) -> None:
        binding_id = binding["binding_id"]
        port = self._allocate_port()
        env = self._build_worker_env(binding)
        env["PORT"] = str(port)

        try:
            process = self._spawn(binding_id, port, env)
        except Exception as exc:  # noqa: BLE001
            self._free_port(port)
            log.error("failed to start worker for binding %s: %s", binding_id, exc)
            self._workers[binding_id] = WorkerEntry(
                binding_id=binding_id,
                runtime_id=str(binding.get("runtime_id") or ""),
                capital_pool_id=str(binding.get("capital_pool_id") or ""),
                port=port,
                process=None,
                started_at=_iso_now(),
                restart_count=restart_count,
                last_error=str(exc),
                status="dead",
            )
            return

        entry = WorkerEntry(
            binding_id=binding_id,
            runtime_id=str(binding.get("runtime_id") or ""),
            capital_pool_id=str(binding.get("capital_pool_id") or ""),
            port=port,
            process=process,
            started_at=_iso_now(),
            restart_count=restart_count,
            status="running",
        )
        self._workers[binding_id] = entry
        log.info(
            "started worker for binding %s runtime=%s port=%d restarts=%d",
            binding_id,
            entry.runtime_id,
            port,
            restart_count,
        )

    def _spawn(
        self, binding_id: str, port: int, env: Dict[str, str]
    ) -> subprocess.Popen:
        """Spawn the paper runtime subprocess. Override in tests."""
        cmd = [sys.executable, self._worker_script]
        return subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
        )

    def _terminate_worker(self, binding_id: str, reason: str = "") -> None:
        entry = self._workers.get(binding_id)
        if entry is None:
            return
        proc = entry.process
        if proc is not None and proc.poll() is None:
            log.info(
                "terminating worker for binding %s (reason: %s)", binding_id, reason
            )
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=self._drain_timeout)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            except OSError:
                pass
            entry.last_exit_code = proc.returncode
        entry.status = "stopped"
        self._free_port(entry.port)
        del self._workers[binding_id]

    # ------------------------------------------------------------------
    # Runtime-manager polling
    # ------------------------------------------------------------------

    def _fetch_active_paper_bindings(self) -> Optional[List[Dict[str, Any]]]:
        """Return the active paper bindings, or None if the fetch failed.

        Callers must treat None as "unknown desired state" and must not modify
        the running fleet (no starts, no stops) when None is returned.
        An empty list means the runtime-manager is reachable but has no active
        paper bindings; in that case the fleet should be wound down normally.
        """
        if not self._url:
            return []
        try:
            import urllib.error
            import urllib.request

            headers: Dict[str, str] = {"Accept": "application/json"}
            if self._token:
                headers["Authorization"] = f"Bearer {self._token}"
            req = urllib.request.Request(
                f"{self._url}/api/runtime-bindings",
                headers=headers,
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            bindings = payload.get("bindings", []) if isinstance(payload, dict) else []
            return [
                b for b in bindings
                if b.get("deployment_mode") == "paper"
                and b.get("status") == "active"
            ]
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._last_error = f"binding fetch failed: {type(exc).__name__}: {exc}"
            log.warning("could not fetch bindings from runtime-manager: %s", exc)
            return None

    # ------------------------------------------------------------------
    # State snapshot
    # ------------------------------------------------------------------

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return self._snapshot()

    def _snapshot(self) -> Dict[str, Any]:
        workers = []
        for entry in self._workers.values():
            pid = entry.process.pid if entry.process is not None else None
            workers.append(
                {
                    "binding_id": entry.binding_id,
                    "runtime_id": entry.runtime_id,
                    "capital_pool_id": entry.capital_pool_id,
                    "port": entry.port,
                    "pid": pid,
                    "status": entry.status,
                    "started_at": entry.started_at,
                    "restart_count": entry.restart_count,
                    "last_exit_code": entry.last_exit_code,
                    "last_error": entry.last_error,
                }
            )
        running = sum(1 for w in workers if w["status"] == "running")
        return {
            "reconciler": "paper_fleet_reconciler",
            "started_at": self._started_at,
            "last_reconcile_at": self._last_reconcile_at,
            "cycle_count": self._cycle_count,
            "last_error": self._last_error,
            "poll_interval_seconds": self._poll_interval,
            "runtime_manager_url": self._url or None,
            "worker_count": len(workers),
            "running_count": running,
            "workers": workers,
        }

    def is_ready(self) -> bool:
        return self._cycle_count > 0


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_RECONCILER: Optional[PaperFleetReconciler] = None


def get_reconciler() -> PaperFleetReconciler:
    global _RECONCILER
    if _RECONCILER is None:
        _RECONCILER = PaperFleetReconciler()
    return _RECONCILER


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        return

    def _write_json(self, status_code: int, body: Dict[str, Any]) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        recon = get_reconciler()
        if self.path in {"/livez"}:
            self._write_json(200, {"status": "live"})
            return
        if self.path in {"/", "/healthz", "/readyz"}:
            snap = recon.snapshot()
            ready = recon.is_ready() and snap.get("last_error") is None
            code = 200 if ready else 503
            if self.path == "/livez":
                code = 200
            self._write_json(code, {**snap, "ready": ready, "live": True})
            return
        if self.path == "/api/fleet/state":
            self._write_json(200, recon.snapshot())
            return
        self._write_json(404, {"status": "not_found", "path": self.path})


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    recon = get_reconciler()
    recon.start()
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8011"))
    server = ThreadingHTTPServer((host, port), _Handler)
    print(
        json.dumps(
            {
                "message": "paper fleet reconciler ready",
                "reconciler": "paper_fleet_reconciler",
                "port": port,
                "runtime_manager_url": recon._url or None,
                "poll_interval_seconds": recon._poll_interval,
            }
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    finally:
        recon.stop()


if __name__ == "__main__":
    main()

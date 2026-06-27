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
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[2]
_DEFAULT_WORKER_SCRIPT = str(
    _HERE.parent / "lean_runtime" / "paper_runtime.py"
)

log = logging.getLogger(__name__)


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_rfc3339(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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
    monitoring_session_id: Optional[str] = None
    restart_count: int = 0
    last_exit_code: Optional[int] = None
    last_error: Optional[str] = None
    status: str = "running"  # running | dead | restarting | stopped
    dead_at: Optional[float] = None  # time.monotonic() when process last exited
    last_heartbeat_at: Optional[str] = None
    heartbeat_status: Optional[str] = None


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
        telemetry_api_url: Optional[str] = None,
        monitoring_session_store_path: Optional[str] = None,
        monitoring_heartbeat_stale_after_seconds: Optional[int] = None,
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
        self._telemetry_url = (
            telemetry_api_url
            or os.getenv("PANTHEON_TELEMETRY_API_URL", "")
            or os.getenv("PANTHEON_TELEMETRY_URL", "")
        ).rstrip("/")
        store_path = (
            monitoring_session_store_path
            or os.getenv("PANTHEON_PAPER_RUNTIME_MONITORING_SESSION_STORE", "")
            or os.getenv("PAPER_RUNTIME_MONITORING_SESSION_STORE", "")
        )
        self._monitoring_session_store_path = Path(store_path) if store_path else None
        stale_after = (
            monitoring_heartbeat_stale_after_seconds
            if monitoring_heartbeat_stale_after_seconds is not None
            else _as_int(
                os.getenv("RECONCILER_MONITORING_HEARTBEAT_STALE_SECONDS")
                or os.getenv("TELEMETRY_RUNTIME_HEARTBEAT_STALE_SECONDS"),
                90,
            )
        )
        self._monitoring_heartbeat_stale_after = max(int(stale_after), 1)
        self._extra_env: Dict[str, str] = dict(extra_env or {})

        self._lock = threading.RLock()
        self._workers: Dict[str, WorkerEntry] = {}
        self._monitoring_sessions: Dict[str, Dict[str, Any]] = {}
        self._used_ports: set[int] = set()
        self._reconcile_thread: Optional[threading.Thread] = None
        self._shutdown = threading.Event()
        self._started_at = _iso_now()
        self._cycle_count = 0
        self._last_reconcile_at: Optional[str] = None
        self._last_error: Optional[str] = None
        self._monitoring_last_error: Optional[str] = None
        self._load_monitoring_sessions()

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
        fleet = self._fetch_fleet_state()
        # fleet is None when the fetch failed — must not evict existing workers
        # in that case, since we have no reliable picture of desired state.
        bindings = fleet[0] if fleet is not None else None
        excluded_ids = fleet[1] if fleet is not None else set()

        runtime_summaries = (
            self._fetch_runtime_summaries()
            if bindings is not None
            else None
        )

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
                    self._end_monitoring_session(
                        entry.monitoring_session_id,
                        reason="worker_exit",
                    )
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
                self._reap_stale_monitoring_sessions(runtime_summaries)

                # Start workers for new or dead-but-desired active bindings
                for binding_id, binding in desired.items():
                    if binding_id not in self._workers:
                        self._start_worker(binding)
                    elif self._workers[binding_id].status == "dead":
                        entry = self._workers[binding_id]
                        # SIGKILL (exit 137) signals an infrastructure disruption
                        # such as OOM or a compose recreate that killed workers
                        # while the reconciler kept running.  Do not count these
                        # against the application-failure restart cap so the fleet
                        # recovers automatically after a deploy event.
                        is_sigkill = entry.last_exit_code == 137
                        effective_restarts = 0 if is_sigkill else entry.restart_count
                        if effective_restarts < self._max_restarts:
                            backoff = effective_restarts * self._restart_backoff
                            ready = (
                                entry.dead_at is None
                                or time.monotonic() >= entry.dead_at + backoff
                            )
                            if ready:
                                self._start_worker(
                                    binding,
                                    restart_count=effective_restarts + 1,
                                )
                        else:
                            log.warning(
                                "binding %s: restart cap reached (%d), not restarting",
                                binding_id,
                                self._max_restarts,
                            )

                # Stop workers for excluded bindings (paused/retired) first,
                # then any remaining workers no longer in the desired fleet.
                for binding_id in list(self._workers):
                    if binding_id in excluded_ids:
                        self._terminate_worker(
                            binding_id,
                            reason="binding excluded from fleet (paused or retired)",
                        )
                    elif binding_id not in desired:
                        self._terminate_worker(
                            binding_id,
                            reason="binding no longer in fleet desired state",
                        )

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
        monitoring_session_id = self._open_monitoring_session(
            binding,
            restart_count=restart_count,
        )

        try:
            process = self._spawn(binding_id, port, env)
        except Exception as exc:  # noqa: BLE001
            self._free_port(port)
            self._end_monitoring_session(
                monitoring_session_id,
                reason="spawn_failed",
                error=str(exc),
            )
            log.error("failed to start worker for binding %s: %s", binding_id, exc)
            self._workers[binding_id] = WorkerEntry(
                binding_id=binding_id,
                runtime_id=str(binding.get("runtime_id") or ""),
                capital_pool_id=str(binding.get("capital_pool_id") or ""),
                port=port,
                process=None,
                started_at=_iso_now(),
                monitoring_session_id=monitoring_session_id,
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
            monitoring_session_id=monitoring_session_id,
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
        self._end_monitoring_session(
            entry.monitoring_session_id,
            reason=reason or "worker_stopped",
        )
        entry.status = "stopped"
        self._free_port(entry.port)
        del self._workers[binding_id]

    # ------------------------------------------------------------------
    # Monitoring sessions
    # ------------------------------------------------------------------

    def _load_monitoring_sessions(self) -> None:
        path = self._monitoring_session_store_path
        if path is None or not path.exists():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8").strip() or "{}")
        except (OSError, json.JSONDecodeError) as exc:
            self._monitoring_last_error = f"monitoring session load failed: {type(exc).__name__}: {exc}"
            return
        records = payload.get("monitoring_sessions") if isinstance(payload, dict) else payload
        if isinstance(records, dict):
            iterable = records.values()
        elif isinstance(records, list):
            iterable = records
        else:
            iterable = []
        normalized: Dict[str, Dict[str, Any]] = {}
        for item in iterable:
            if not isinstance(item, dict):
                continue
            session_id = str(item.get("session_id") or item.get("id") or "").strip()
            if session_id:
                normalized[session_id] = dict(item)
        self._monitoring_sessions = normalized

    def _persist_monitoring_sessions(self) -> None:
        path = self._monitoring_session_store_path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        records = sorted(
            self._monitoring_sessions.values(),
            key=lambda item: (
                str(item.get("started_at") or ""),
                str(item.get("session_id") or ""),
            ),
        )
        payload = {"monitoring_sessions": records}
        tmp_path = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp_path, path)

    @staticmethod
    def _monitoring_session_open(session: Dict[str, Any]) -> bool:
        if session.get("ended_at") not in (None, ""):
            return False
        status = str(session.get("status") or "").strip().lower()
        return status not in {"ended", "stale", "failed"}

    def _open_monitoring_session(
        self,
        binding: Dict[str, Any],
        *,
        restart_count: int,
    ) -> str:
        binding_id = str(binding.get("binding_id") or "")
        now = _iso_now()
        for session_id, session in list(self._monitoring_sessions.items()):
            if (
                str(session.get("binding_id") or session.get("runtime_binding_id") or "") == binding_id
                and self._monitoring_session_open(session)
            ):
                self._end_monitoring_session(
                    session_id,
                    reason="superseded_by_restart",
                    ended_at=now,
                )
        session_id = f"prmon-{binding_id}-{uuid.uuid4().hex[:8]}"
        session = {
            "id": session_id,
            "session_id": session_id,
            "session_type": "paper_runtime_monitoring",
            "binding_id": binding_id,
            "runtime_binding_id": binding_id,
            "runtime_id": str(binding.get("runtime_id") or ""),
            "capital_pool_id": str(binding.get("capital_pool_id") or ""),
            "deployment_stage": "paper",
            "status": "running",
            "started_at": now,
            "ended_at": None,
            "restart_count": restart_count,
            "stale_after_seconds": self._monitoring_heartbeat_stale_after,
            "last_heartbeat_at": None,
            "ended_reason": None,
        }
        self._monitoring_sessions[session_id] = session
        self._persist_monitoring_sessions()
        return session_id

    def _end_monitoring_session(
        self,
        session_id: Optional[str],
        *,
        reason: str,
        ended_at: Optional[str] = None,
        staleness: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        if not session_id:
            return
        session = self._monitoring_sessions.get(session_id)
        if not session or not self._monitoring_session_open(session):
            return
        session["ended_at"] = ended_at or _iso_now()
        session["status"] = "ended"
        session["ended_reason"] = reason
        if staleness is not None:
            session["staleness"] = dict(staleness)
        if error:
            session["last_error"] = error
        self._persist_monitoring_sessions()

    def _monitoring_staleness(
        self,
        session: Dict[str, Any],
        summary: Optional[Dict[str, Any]],
        *,
        now: datetime,
    ) -> Optional[Dict[str, Any]]:
        heartbeat_at_raw = None
        if summary:
            heartbeat_at_raw = summary.get("last_heartbeat_at")
        heartbeat_at_raw = heartbeat_at_raw or session.get("last_heartbeat_at")
        heartbeat_at = _parse_rfc3339(heartbeat_at_raw)
        if heartbeat_at is None:
            started_at = _parse_rfc3339(session.get("started_at"))
            if started_at is None:
                return None
            age_seconds = (now - started_at).total_seconds()
            if age_seconds <= self._monitoring_heartbeat_stale_after:
                return None
            return {
                "status": "stale",
                "reason": "missing_heartbeat",
                "last_known_at": session.get("started_at"),
                "age_seconds": int(age_seconds),
                "threshold_seconds": self._monitoring_heartbeat_stale_after,
            }

        age_seconds = (now - heartbeat_at).total_seconds()
        if age_seconds <= self._monitoring_heartbeat_stale_after:
            return None
        return {
            "status": "stale",
            "reason": "stale_heartbeat",
            "last_known_at": str(heartbeat_at_raw),
            "age_seconds": int(age_seconds),
            "threshold_seconds": self._monitoring_heartbeat_stale_after,
        }

    def _reap_stale_monitoring_sessions(
        self,
        summaries: Optional[Dict[str, Dict[str, Any]]],
    ) -> None:
        if summaries is None:
            return
        now = datetime.now(timezone.utc)
        stale_binding_ids: List[str] = []
        changed = False
        for session_id, session in list(self._monitoring_sessions.items()):
            if not self._monitoring_session_open(session):
                continue
            runtime_id = str(session.get("runtime_id") or "")
            summary = summaries.get(runtime_id)
            if summary and summary.get("last_heartbeat_at"):
                session["last_heartbeat_at"] = summary.get("last_heartbeat_at")
                session["heartbeat_status"] = summary.get("state") or summary.get("connectivity_status") or "active"
                binding_id = str(session.get("binding_id") or session.get("runtime_binding_id") or "")
                entry = self._workers.get(binding_id)
                if entry is not None:
                    entry.last_heartbeat_at = str(summary.get("last_heartbeat_at"))
                    entry.heartbeat_status = str(session["heartbeat_status"])
                changed = True
            staleness = self._monitoring_staleness(session, summary, now=now)
            if staleness is None:
                continue
            self._end_monitoring_session(
                session_id,
                reason=staleness["reason"],
                staleness=staleness,
            )
            changed = False
            binding_id = str(session.get("binding_id") or session.get("runtime_binding_id") or "")
            if binding_id:
                stale_binding_ids.append(binding_id)
        if changed:
            self._persist_monitoring_sessions()
        for binding_id in stale_binding_ids:
            if binding_id in self._workers:
                self._terminate_worker(
                    binding_id,
                    reason="monitoring heartbeat stale",
                )

    def _fetch_runtime_summaries(self) -> Optional[Dict[str, Dict[str, Any]]]:
        if not self._telemetry_url:
            return None
        try:
            import urllib.request

            req = urllib.request.Request(
                f"{self._telemetry_url}/api/telemetry/runtime-summaries",
                headers={"Accept": "application/json"},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            summaries = payload.get("summaries", []) if isinstance(payload, dict) else []
            normalized = {
                str(item.get("runtime_id") or item.get("id") or ""): item
                for item in summaries
                if isinstance(item, dict) and str(item.get("runtime_id") or item.get("id") or "").strip()
            }
            self._monitoring_last_error = None
            return normalized
        except Exception as exc:  # noqa: BLE001
            self._monitoring_last_error = f"runtime summary fetch failed: {type(exc).__name__}: {exc}"
            log.warning("could not fetch runtime summaries from telemetry: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Runtime-manager polling
    # ------------------------------------------------------------------

    def _fetch_fleet_state(
        self,
    ) -> Optional[Tuple[List[Dict[str, Any]], Set[str]]]:
        """Fetch the canonical fleet desired state from the runtime-manager.

        Calls the LOOP-AUTO-RT-001 stable endpoint:
            GET /api/runtime-fleet/desired-state?stage=paper&include_excluded=true

        Returns
        -------
        (desired_bindings, excluded_binding_ids) on success:
            desired_bindings    — list of active paper RuntimeBinding dicts the
                                  reconciler must run exactly one worker for
            excluded_binding_ids — set of binding_ids that must be stopped
                                  (paused, retired, failed, or draining)
        None when the runtime-manager is unreachable or returns an error —
        callers must treat None as "unknown desired state" and must not modify
        the running fleet (no starts, no stops).
        """
        if not self._url:
            return ([], set())
        try:
            import urllib.request

            headers: Dict[str, str] = {"Accept": "application/json"}
            if self._token:
                headers["Authorization"] = f"Bearer {self._token}"
            req = urllib.request.Request(
                f"{self._url}/api/runtime-fleet/desired-state"
                "?stage=paper&include_excluded=true",
                headers=headers,
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            bindings = payload.get("bindings", []) if isinstance(payload, dict) else []
            excluded = payload.get("excluded", []) if isinstance(payload, dict) else []
            excluded_ids: Set[str] = {
                str(e["binding_id"])
                for e in excluded
                if isinstance(e, dict) and e.get("binding_id")
            }
            return (bindings, excluded_ids)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._last_error = (
                    f"fleet desired state fetch failed: {type(exc).__name__}: {exc}"
                )
            log.warning(
                "could not fetch fleet desired state from runtime-manager: %s", exc
            )
            return None

    def _fetch_active_paper_bindings(self) -> Optional[List[Dict[str, Any]]]:
        """Return the active paper bindings, or None if the fetch failed.

        Deprecated: use _fetch_fleet_state() to get both desired and excluded
        bindings from the canonical LOOP-AUTO-RT-001 endpoint.  This method is
        retained for subclasses and tests that override it directly.
        """
        result = self._fetch_fleet_state()
        if result is None:
            return None
        return result[0]

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
                    "monitoring_session_id": entry.monitoring_session_id,
                    "restart_count": entry.restart_count,
                    "last_exit_code": entry.last_exit_code,
                    "last_error": entry.last_error,
                    "last_heartbeat_at": entry.last_heartbeat_at,
                    "heartbeat_status": entry.heartbeat_status,
                }
            )
        running = sum(1 for w in workers if w["status"] == "running")
        monitoring_sessions = [
            {
                **session,
                "active": self._monitoring_session_open(session),
            }
            for session in sorted(
                self._monitoring_sessions.values(),
                key=lambda item: (
                    str(item.get("started_at") or ""),
                    str(item.get("session_id") or ""),
                ),
            )
        ]
        return {
            "reconciler": "paper_fleet_reconciler",
            "started_at": self._started_at,
            "last_reconcile_at": self._last_reconcile_at,
            "cycle_count": self._cycle_count,
            "last_error": self._last_error,
            "monitoring_last_error": self._monitoring_last_error,
            "poll_interval_seconds": self._poll_interval,
            "runtime_manager_url": self._url or None,
            "telemetry_api_url": self._telemetry_url or None,
            "monitoring_heartbeat_stale_after_seconds": self._monitoring_heartbeat_stale_after,
            "monitoring_session_store_path": (
                str(self._monitoring_session_store_path)
                if self._monitoring_session_store_path
                else None
            ),
            "worker_count": len(workers),
            "running_count": running,
            "workers": workers,
            "monitoring_session_count": len(monitoring_sessions),
            "active_monitoring_session_count": len([
                session for session in monitoring_sessions if session.get("active")
            ]),
            "monitoring_sessions": monitoring_sessions,
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

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
  PANTHEON_SOURCE_INGEST_URL        market-mark source forwarded to each worker
  PANTHEON_PERFORMANCE_MARK_MAX_AGE_SECONDS
                                     maximum accepted market-mark age (default 172800)
  PANTHEON_PERFORMANCE_STATE_ROOT   persistent per-binding ledger directory
  SIGNAL_STORE_URL                  forwarded to each worker
  WORKER_SCRIPT_PATH                override path to paper_runtime.py
  HOST / PORT                       reconciler HTTP interface (default 0.0.0.0/8011)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
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

import fcntl

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services.execution.market_snapshot_admission import (
    SnapshotAdmissionDecision,
    admit_market_snapshot,
    parse_rfc3339 as _admission_parse_rfc3339,
)

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


def _binding_state_filename(binding_id: str) -> str:
    """Return a stable filename without allowing binding IDs to shape paths."""
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", binding_id).strip("._-")
    slug = (normalized or "binding")[:48]
    digest = hashlib.sha256(binding_id.encode("utf-8")).hexdigest()[:12]
    return f"{slug}-{digest}.json"


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def validate_executable_binding(binding: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Validate if a paper RuntimeBinding has all required execution fields.
    
    Returns (is_valid, rejection_reason). If non-executable, rejection_reason
    is a typed string describing the missing field.
    """
    if not isinstance(binding, dict):
        return False, "invalid_binding_payload"
    
    required_fields = {
        "binding_id": "missing_binding_id",
        "runtime_id": "missing_runtime_id",
        "artifact_id": "missing_artifact_id",
        "artifact_version": "missing_artifact_version",
        "capital_pool_id": "missing_capital_pool_id",
        "plan_id": "missing_plan_id",
    }
    
    for field, reason in required_fields.items():
        val = binding.get(field)
        if val is None or not str(val).strip():
            return False, reason

    return True, None


def _binding_persona_id(binding: Dict[str, Any]) -> str:
    metadata = binding.get("metadata") if isinstance(binding.get("metadata"), dict) else {}
    candidates = (
        binding.get("persona_id"),
        binding.get("sponsor_persona_id"),
        metadata.get("persona_id"),
        metadata.get("sponsor_persona_id"),
    )
    for candidate in candidates:
        cleaned = _clean_text(candidate)
        if cleaned:
            return cleaned
    return ""


# ---------------------------------------------------------------------------
# Durable, fenced leader lease
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FencedLease:
    acquired: bool
    token: int
    expires_at_ms: int


class RedisFencedLeaderStore:
    """Atomic Redis leader lease with a monotonically increasing fence token."""

    kind = "redis_fenced_leader_store"

    _ACQUIRE_OR_RENEW_LUA = """
local clock = redis.call('TIME')
local now_ms = (tonumber(clock[1]) * 1000) + math.floor(tonumber(clock[2]) / 1000)
local holder = redis.call('HGET', KEYS[1], 'holder')
local token = tonumber(redis.call('HGET', KEYS[1], 'token') or '0')
local expires_at_ms = tonumber(redis.call('HGET', KEYS[1], 'expires_at_ms') or '0')
local requested_token = tonumber(ARGV[2] or '0')
local ttl_ms = tonumber(ARGV[3])

if holder == ARGV[1] and token == requested_token and expires_at_ms > now_ms then
  expires_at_ms = now_ms + ttl_ms
  redis.call('HSET', KEYS[1], 'expires_at_ms', expires_at_ms)
  redis.call('PEXPIRE', KEYS[1], ttl_ms * 2)
  return {1, token, expires_at_ms}
end

if (not holder) or expires_at_ms <= now_ms then
  token = redis.call('INCR', KEYS[2])
  expires_at_ms = now_ms + ttl_ms
  redis.call(
    'HSET', KEYS[1],
    'holder', ARGV[1],
    'token', token,
    'expires_at_ms', expires_at_ms
  )
  redis.call('PEXPIRE', KEYS[1], ttl_ms * 2)
  return {1, token, expires_at_ms}
end

return {0, token, expires_at_ms}
"""

    _VALIDATE_LUA = """
local clock = redis.call('TIME')
local now_ms = (tonumber(clock[1]) * 1000) + math.floor(tonumber(clock[2]) / 1000)
local holder = redis.call('HGET', KEYS[1], 'holder')
local token = tonumber(redis.call('HGET', KEYS[1], 'token') or '0')
local expires_at_ms = tonumber(redis.call('HGET', KEYS[1], 'expires_at_ms') or '0')
if holder == ARGV[1] and token == tonumber(ARGV[2]) and expires_at_ms > now_ms then
  return 1
end
return 0
"""

    _RELEASE_LUA = """
local holder = redis.call('HGET', KEYS[1], 'holder')
local token = tonumber(redis.call('HGET', KEYS[1], 'token') or '0')
if holder == ARGV[1] and token == tonumber(ARGV[2]) then
  redis.call('DEL', KEYS[1])
  return 1
end
return 0
"""

    def __init__(
        self,
        client: Any,
        *,
        lease_key: str = "pantheon:paper-fleet-reconciler:leader",
    ) -> None:
        self._client = client
        self._lease_key = lease_key
        self._counter_key = f"{lease_key}:fence-counter"

    def acquire_or_renew(
        self,
        holder: str,
        current_token: int,
        ttl_seconds: float,
    ) -> FencedLease:
        result = self._client.eval(
            self._ACQUIRE_OR_RENEW_LUA,
            2,
            self._lease_key,
            self._counter_key,
            holder,
            int(current_token),
            max(int(ttl_seconds * 1000), 100),
        )
        return FencedLease(
            acquired=bool(int(result[0])),
            token=int(result[1]),
            expires_at_ms=int(result[2]),
        )

    def validate(self, holder: str, token: int) -> bool:
        return bool(
            int(
                self._client.eval(
                    self._VALIDATE_LUA,
                    1,
                    self._lease_key,
                    holder,
                    int(token),
                )
            )
        )

    def release(self, holder: str, token: int) -> bool:
        return bool(
            int(
                self._client.eval(
                    self._RELEASE_LUA,
                    1,
                    self._lease_key,
                    holder,
                    int(token),
                )
            )
        )


class FileFencedLeaderStore:
    """Process-safe file lease for single-host deployments and tests."""

    kind = "file_fenced_leader_store"

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock_path = self._path.with_name(f"{self._path.name}.lock")

    def _read(self) -> Dict[str, Any]:
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write(self, payload: Dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_name(
            f".{self._path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self._path)

    def _locked(self):
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = self._lock_path.open("a+", encoding="utf-8")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        return handle

    def acquire_or_renew(
        self,
        holder: str,
        current_token: int,
        ttl_seconds: float,
    ) -> FencedLease:
        lock_handle = self._locked()
        try:
            now_ms = int(time.time() * 1000)
            data = self._read()
            stored_holder = str(data.get("holder") or "")
            stored_token = int(data.get("token") or 0)
            expires_at_ms = int(data.get("expires_at_ms") or 0)
            if (
                stored_holder == holder
                and stored_token == int(current_token)
                and expires_at_ms > now_ms
            ):
                expires_at_ms = now_ms + max(int(ttl_seconds * 1000), 100)
                self._write(
                    {
                        "holder": holder,
                        "token": stored_token,
                        "expires_at_ms": expires_at_ms,
                    }
                )
                return FencedLease(True, stored_token, expires_at_ms)
            if not stored_holder or expires_at_ms <= now_ms:
                next_token = stored_token + 1
                expires_at_ms = now_ms + max(int(ttl_seconds * 1000), 100)
                self._write(
                    {
                        "holder": holder,
                        "token": next_token,
                        "expires_at_ms": expires_at_ms,
                    }
                )
                return FencedLease(True, next_token, expires_at_ms)
            return FencedLease(False, stored_token, expires_at_ms)
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            lock_handle.close()

    def validate(self, holder: str, token: int) -> bool:
        lock_handle = self._locked()
        try:
            data = self._read()
            return (
                str(data.get("holder") or "") == holder
                and int(data.get("token") or 0) == int(token)
                and int(data.get("expires_at_ms") or 0) > int(time.time() * 1000)
            )
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            lock_handle.close()

    def release(self, holder: str, token: int) -> bool:
        lock_handle = self._locked()
        try:
            data = self._read()
            if (
                str(data.get("holder") or "") != holder
                or int(data.get("token") or 0) != int(token)
            ):
                return False
            self._write(
                {
                    "holder": "",
                    "token": int(token),
                    "expires_at_ms": 0,
                }
            )
            return True
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            lock_handle.close()


class InMemoryFencedLeaderStore:
    """Thread-safe unit-test backend; production uses Redis or a locked file."""

    kind = "memory_fenced_leader_store"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: Dict[str, Any] = {}

    def acquire_or_renew(
        self,
        holder: str,
        current_token: int,
        ttl_seconds: float,
    ) -> FencedLease:
        with self._lock:
            now_ms = int(time.time() * 1000)
            stored_holder = str(self._state.get("holder") or "")
            stored_token = int(self._state.get("token") or 0)
            expires_at_ms = int(self._state.get("expires_at_ms") or 0)
            if (
                stored_holder == holder
                and stored_token == int(current_token)
                and expires_at_ms > now_ms
            ):
                expires_at_ms = now_ms + max(int(ttl_seconds * 1000), 100)
                self._state["expires_at_ms"] = expires_at_ms
                return FencedLease(True, stored_token, expires_at_ms)
            if not stored_holder or expires_at_ms <= now_ms:
                stored_token += 1
                expires_at_ms = now_ms + max(int(ttl_seconds * 1000), 100)
                self._state = {
                    "holder": holder,
                    "token": stored_token,
                    "expires_at_ms": expires_at_ms,
                }
                return FencedLease(True, stored_token, expires_at_ms)
            return FencedLease(False, stored_token, expires_at_ms)

    def validate(self, holder: str, token: int) -> bool:
        with self._lock:
            return (
                str(self._state.get("holder") or "") == holder
                and int(self._state.get("token") or 0) == int(token)
                and int(self._state.get("expires_at_ms") or 0)
                > int(time.time() * 1000)
            )

    def release(self, holder: str, token: int) -> bool:
        with self._lock:
            if (
                str(self._state.get("holder") or "") != holder
                or int(self._state.get("token") or 0) != int(token)
            ):
                return False
            self._state["holder"] = ""
            self._state["expires_at_ms"] = 0
            return True


def _coerce_leader_store(store: Any | None) -> Any | None:
    if store is None:
        return None
    if all(hasattr(store, method) for method in ("acquire_or_renew", "validate", "release")):
        return store
    if isinstance(store, (str, Path)):
        return FileFencedLeaderStore(store)
    if hasattr(store, "eval"):
        return RedisFencedLeaderStore(store)
    raise TypeError("leader_store must be a fenced Redis/file lease backend")


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
    fence_token: Optional[int] = None


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
        telemetry_service_token: Optional[str] = None,
        telemetry_tenant_id: Optional[str] = None,
        source_ingest_url: Optional[str] = None,
        performance_mark_max_age_seconds: Optional[int] = None,
        performance_state_root: Optional[str] = None,
        monitoring_session_store_path: Optional[str] = None,
        monitoring_heartbeat_stale_after_seconds: Optional[int] = None,
        leader_store: Optional[Any] = None,
        leader_lease_ttl_seconds: Optional[float] = None,
        reconciler_id: Optional[str] = None,
        store: Optional[Any] = None,
        extra_env: Optional[Dict[str, str]] = None,
    ) -> None:
        self._store = store
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
        self._telemetry_service_token = (
            telemetry_service_token
            if telemetry_service_token is not None
            else os.getenv("PANTHEON_TELEMETRY_SERVICE_TOKEN", "")
        ).strip()
        self._telemetry_tenant_id = (
            telemetry_tenant_id
            if telemetry_tenant_id is not None
            else os.getenv("PANTHEON_TENANT_ID", "default")
        ).strip()
        self._source_ingest_url = (
            source_ingest_url
            or os.getenv("PANTHEON_SOURCE_INGEST_URL", "")
            or os.getenv("PANTHEON_SOURCE_INGEST_API_URL", "")
        ).rstrip("/")
        mark_max_age = (
            performance_mark_max_age_seconds
            if performance_mark_max_age_seconds is not None
            else _as_int(
                os.getenv("PANTHEON_PERFORMANCE_MARK_MAX_AGE_SECONDS"),
                172800,
            )
        )
        self._performance_mark_max_age_seconds = max(int(mark_max_age), 1)
        self._performance_state_root = Path(
            performance_state_root
            or os.getenv("PANTHEON_PERFORMANCE_STATE_ROOT", "")
            or "/data/runtime/paper-performance"
        )
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

        # Leader ownership fails closed until a durable backend grants a
        # monotonically fenced lease.  Unit tests opt into the explicit
        # InMemoryFencedLeaderStore; the production singleton always uses Redis
        # (or an explicitly configured locked file backend).
        self._reconciler_id = reconciler_id or f"reconciler-{uuid.uuid4().hex[:8]}"
        self._is_leader = False
        self._leader_store = _coerce_leader_store(leader_store)
        self._leader_lease_ttl = max(
            float(
                leader_lease_ttl_seconds
                if leader_lease_ttl_seconds is not None
                else _as_float(os.getenv("RECONCILER_LEADER_LEASE_TTL_SECONDS"), 30.0)
            ),
            0.1,
        )
        self._fence_token = 0
        self._lease_expires_at_ms = 0

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

    @property
    def reconciler_id(self) -> str:
        return self._reconciler_id

    @property
    def is_leader(self) -> bool:
        with self._lock:
            return self._is_leader

    def try_acquire_lease(self, leader_store: Any | None = None) -> bool:
        """Atomically acquire or renew the reconciler's token-fenced lease."""
        with self._lock:
            if leader_store is not None:
                self._leader_store = _coerce_leader_store(leader_store)
            store = self._leader_store
            if store is None:
                self._is_leader = False
                self._last_error = "durable fenced leader store is not configured"
                return False
            try:
                lease = store.acquire_or_renew(
                    self._reconciler_id,
                    self._fence_token,
                    self._leader_lease_ttl,
                )
            except Exception as exc:  # noqa: BLE001 - lease failure must fail closed
                self._is_leader = False
                self._last_error = f"leader lease unavailable: {type(exc).__name__}: {exc}"
                return False
            self._is_leader = bool(lease.acquired)
            if self._is_leader:
                self._fence_token = int(lease.token)
                self._lease_expires_at_ms = int(lease.expires_at_ms)
            return self._is_leader

    def _has_current_fence(self) -> bool:
        store = self._leader_store
        if store is None or not self._is_leader or self._fence_token <= 0:
            return False
        try:
            return bool(store.validate(self._reconciler_id, self._fence_token))
        except Exception:  # noqa: BLE001 - any ambiguity fails closed
            return False

    def _require_current_fence(self) -> None:
        if not self.try_acquire_lease():
            raise RuntimeError("reconciler lost fenced leader ownership")

    def _demote_and_stop_workers(self) -> None:
        with self._lock:
            self._is_leader = False
            for binding_id in list(self._workers):
                self._terminate_worker(
                    binding_id,
                    reason="reconciler lost fenced leader lease",
                    enforce_fence=False,
                )

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
                self._terminate_worker(
                    binding_id,
                    reason="reconciler shutdown",
                    enforce_fence=False,
                )
            if self._leader_store is not None and self._fence_token > 0:
                try:
                    self._leader_store.release(
                        self._reconciler_id,
                        self._fence_token,
                    )
                except Exception:  # noqa: BLE001 - expiry remains the failover path
                    pass
            self._is_leader = False

    # ------------------------------------------------------------------
    # Reconcile
    # ------------------------------------------------------------------

    def reconcile_once(self, leader_store: Any | None = None) -> Dict[str, Any]:
        """Run one reconcile cycle. Returns a snapshot of the fleet state."""
        if not self.try_acquire_lease(leader_store):
            log.info("reconciler %s is follower; skipping worker spawn cycle", self._reconciler_id)
            self._demote_and_stop_workers()
            return self.snapshot()

        fleet = self._fetch_fleet_state()
        # fleet is None when the fetch failed — must not evict existing workers
        # in that case, since we have no reliable picture of desired state.
        bindings = fleet[0] if fleet is not None else None
        excluded_ids = set(fleet[1]) if fleet is not None and len(fleet) > 1 else set()
        raw_excluded = list(fleet[2]) if fleet is not None and len(fleet) > 2 else []

        runtime_summaries = (
            self._fetch_runtime_summaries()
            if bindings is not None
            else None
        )
        if not self._has_current_fence():
            self._demote_and_stop_workers()
            return self.snapshot()

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
                # 1. Admission defense on active bindings (SD-PAPER-01 §7.2)
                for binding in list(bindings):
                    binding_id = str(binding.get("binding_id") or "")
                    decision = self._check_market_admission(binding)
                    if decision is not None and not decision.admitted:
                        log.warning(
                            "pausing active binding %s due to market admission rejection (%s: %s)",
                            binding_id,
                            decision.reason_code,
                            decision.detail,
                        )
                        metadata = binding.get("metadata") if isinstance(binding.get("metadata"), dict) else {}
                        policy = binding.get("market_data_policy") or metadata.get("market_data_policy") or {}
                        max_age = int(
                            policy.get("max_age_seconds")
                            or self._performance_mark_max_age_seconds
                            or 86400
                        )
                        pause_cmd_ref = f"cmd-stale-pause-{binding_id}-{uuid.uuid4().hex[:8]}"
                        stale_patch = {
                            "session_admission": {
                                "reason_code": decision.reason_code or "market_input_stale",
                                "source_snapshot_id": decision.snapshot_id,
                                "source_event_time": decision.event_time,
                                "observed_at": _iso_now(),
                                "max_age_seconds": max_age,
                                "pause_command_ref": pause_cmd_ref,
                                "resume_snapshot_id": None,
                                "resumed_at": None,
                            }
                        }
                        # active -> pending_pause -> paused
                        self._transition_binding(binding_id, "pending_pause", metadata_patch=stale_patch)
                        self._transition_binding(binding_id, "paused", metadata_patch=stale_patch)
                        self._terminate_worker(binding_id, reason=f"paused due to {decision.reason_code}")
                        bindings.remove(binding)
                        excluded_ids.add(binding_id)
                        binding["status"] = "paused"
                        binding.setdefault("metadata", {})["session_admission"] = stale_patch["session_admission"]
                        raw_excluded.append(binding)

                # 2. Resume defense on paused bindings (SD-PAPER-01 §7.3)
                for eb in list(raw_excluded):
                    if isinstance(eb, dict) and eb.get("status") == "paused":
                        binding_id = str(eb.get("binding_id") or "")
                        metadata = eb.get("metadata") if isinstance(eb.get("metadata"), dict) else {}
                        session_adm = metadata.get("session_admission")
                        # Only auto-resume if paused due to stale input — never auto-resume operator pauses
                        if (
                            isinstance(session_adm, dict)
                            and session_adm.get("reason_code") == "market_input_stale"
                        ):
                            paused_snap_id = session_adm.get("source_snapshot_id")
                            paused_time_str = session_adm.get("source_event_time")
                            decision = self._check_market_admission(eb)
                            if decision is not None and decision.admitted:
                                paused_dt, _ = _admission_parse_rfc3339(paused_time_str, field_name="paused_event_time")
                                new_dt, _ = _admission_parse_rfc3339(decision.event_time, field_name="new_event_time")
                                is_different_snap = (decision.snapshot_id != paused_snap_id) if (decision.snapshot_id and paused_snap_id) else True
                                is_later_time = (new_dt > paused_dt) if (new_dt and paused_dt) else False

                                if is_different_snap and is_later_time:
                                    log.info(
                                        "resuming paused binding %s: new admitted snapshot %s (event_time=%s > %s)",
                                        binding_id,
                                        decision.snapshot_id,
                                        decision.event_time,
                                        paused_time_str,
                                    )
                                    resume_patch = {
                                        "session_admission": {
                                            "resume_snapshot_id": decision.snapshot_id,
                                            "resumed_at": _iso_now(),
                                        }
                                    }
                                    # paused -> active
                                    self._transition_binding(binding_id, "active", metadata_patch=resume_patch)
                                    eb["status"] = "active"
                                    eb.setdefault("metadata", {})["session_admission"].update(resume_patch["session_admission"])
                                    if binding_id in excluded_ids:
                                        excluded_ids.remove(binding_id)
                                    bindings.append(eb)
                                    raw_excluded.remove(eb)

                desired: Dict[str, Dict[str, Any]] = {
                    b["binding_id"]: b for b in bindings
                }
                self._reap_stale_monitoring_sessions(runtime_summaries)

                # Start workers for new or dead-but-desired active bindings
                for binding_id, binding in desired.items():
                    if binding_id not in self._workers:
                        if not self._start_worker(binding):
                            self._demote_and_stop_workers()
                            self._cycle_count += 1
                            self._last_reconcile_at = _iso_now()
                            return self._snapshot()
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
                                if not self._start_worker(
                                    binding,
                                    restart_count=effective_restarts + 1,
                                ):
                                    self._demote_and_stop_workers()
                                    self._cycle_count += 1
                                    self._last_reconcile_at = _iso_now()
                                    return self._snapshot()
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
                self.reconcile_once(self._leader_store)
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
                "PANTHEON_RECONCILER_ID": self._reconciler_id,
                "PANTHEON_RECONCILER_FENCE_TOKEN": str(self._fence_token),
            }
        )
        persona_id = _binding_persona_id(binding)
        if persona_id:
            env["PANTHEON_PERSONA_ID"] = persona_id
        if self._url:
            env["PANTHEON_RUNTIME_MANAGER_URL"] = self._url
        if self._token:
            env["PANTHEON_RUNTIME_MANAGER_TOKEN"] = self._token
        if self._source_ingest_url:
            env["PANTHEON_SOURCE_INGEST_URL"] = self._source_ingest_url
        env["PANTHEON_PERFORMANCE_MARK_MAX_AGE_SECONDS"] = str(
            self._performance_mark_max_age_seconds
        )
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
            env["PANTHEON_PERFORMANCE_STATE_PATH"] = str(
                self._performance_state_root / _binding_state_filename(binding_id)
            )
        return env

    def _start_worker(
        self, binding: Dict[str, Any], restart_count: int = 0
    ) -> bool:
        self._require_current_fence()
        is_valid, reason = validate_executable_binding(binding)
        if not is_valid:
            b_id = str(binding.get("binding_id") or "<unknown>") if isinstance(binding, dict) else "<unknown>"
            log.warning("cannot start worker for non-executable binding %s: %s", b_id, reason)
            return True

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
                fence_token=self._fence_token,
            )
            return self._has_current_fence()

        # Spawning is outside the leader store transaction and can block past
        # the lease deadline.  Validate the exact fence again before exposing
        # the child in the worker registry.  A stale leader must compensate by
        # terminating the unregistered child it just created.
        if not self._has_current_fence():
            if process.poll() is None:
                try:
                    process.terminate()
                    try:
                        process.wait(timeout=self._drain_timeout)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                except OSError:
                    pass
            self._free_port(port)
            self._end_monitoring_session(
                monitoring_session_id,
                reason="spawn_fence_lost",
                error="leader fence expired while worker spawn was in progress",
            )
            self._is_leader = False
            self._last_error = (
                f"leader fence expired while spawning worker for binding {binding_id}"
            )
            log.error(self._last_error)
            return False

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
            fence_token=self._fence_token,
        )
        self._workers[binding_id] = entry
        log.info(
            "started worker for binding %s runtime=%s port=%d restarts=%d",
            binding_id,
            entry.runtime_id,
            port,
            restart_count,
        )
        return True

    def _spawn(
        self, binding_id: str, port: int, env: Dict[str, str]
    ) -> subprocess.Popen:
        """Spawn the paper runtime subprocess. Override in tests."""
        cmd = [sys.executable, self._worker_script]
        return subprocess.Popen(
            cmd,
            env=env,
            close_fds=True,
        )

    def _terminate_worker(
        self,
        binding_id: str,
        reason: str = "",
        *,
        enforce_fence: bool = True,
    ) -> None:
        if enforce_fence:
            self._require_current_fence()
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
    def _monitoring_session_staleness_marker(session: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        staleness = session.get("staleness")
        if not isinstance(staleness, dict):
            return None
        marker = dict(staleness)
        status = str(marker.get("status") or "").strip().lower()
        reason = str(marker.get("reason") or "").strip()
        if status == "stale" or reason:
            marker.setdefault("status", "stale")
            return marker
        return None

    @staticmethod
    def _monitoring_session_open(session: Dict[str, Any]) -> bool:
        if session.get("ended_at") not in (None, ""):
            return False
        status = str(session.get("status") or "").strip().lower()
        if status in {"ended", "stale", "failed"}:
            return False
        if PaperFleetReconciler._monitoring_session_staleness_marker(session) is not None:
            return False
        explicit = session.get("active")
        if explicit is not None:
            return bool(explicit)
        return True

    def _open_monitoring_session(
        self,
        binding: Dict[str, Any],
        *,
        restart_count: int,
    ) -> str:
        binding_id = str(binding.get("binding_id") or "")
        now = _iso_now()
        for session_id, session in list(self._monitoring_sessions.items()):
            if str(session.get("binding_id") or session.get("runtime_binding_id") or "") != binding_id:
                continue
            staleness = self._monitoring_session_staleness_marker(session)
            if staleness is not None and session.get("ended_at") in (None, ""):
                self._end_monitoring_session(
                    session_id,
                    reason=str(staleness.get("reason") or "stale_monitoring_session"),
                    ended_at=now,
                    staleness=staleness,
                    force=True,
                )
            elif self._monitoring_session_open(session):
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
        force: bool = False,
    ) -> None:
        if not session_id:
            return
        session = self._monitoring_sessions.get(session_id)
        if not session:
            return
        if session.get("ended_at") not in (None, ""):
            return
        if not force and not self._monitoring_session_open(session):
            return
        session["ended_at"] = ended_at or _iso_now()
        session["status"] = "ended"
        session["ended_reason"] = reason
        session["terminal_reason"] = reason
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
        may_derive_staleness = summaries is not None
        summaries = summaries or {}
        now = datetime.now(timezone.utc)
        stale_binding_ids: List[str] = []
        changed = False
        for session_id, session in list(self._monitoring_sessions.items()):
            existing_staleness = self._monitoring_session_staleness_marker(session)
            if existing_staleness is not None and session.get("ended_at") in (None, ""):
                self._end_monitoring_session(
                    session_id,
                    reason=str(existing_staleness.get("reason") or "stale_monitoring_session"),
                    staleness=existing_staleness,
                    force=True,
                )
                binding_id = str(session.get("binding_id") or session.get("runtime_binding_id") or "")
                if binding_id:
                    stale_binding_ids.append(binding_id)
                continue
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
            if not may_derive_staleness:
                continue
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

            headers = {
                "Accept": "application/json",
                "X-Tenant-Id": self._telemetry_tenant_id,
            }
            if self._telemetry_service_token:
                headers["Authorization"] = (
                    f"Bearer {self._telemetry_service_token}"
                )
            req = urllib.request.Request(
                f"{self._telemetry_url}/api/telemetry/runtime-summaries",
                headers=headers,
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
    ) -> Optional[Tuple[List[Dict[str, Any]], Set[str], List[Dict[str, Any]]]]:
        """Fetch the canonical fleet desired state from the runtime-manager or store.

        Calls the LOOP-AUTO-RT-001 stable endpoint:
            GET /api/runtime-fleet/desired-state?stage=paper&include_excluded=true

        Returns
        -------
        (desired_bindings, excluded_binding_ids, excluded_bindings) on success:
            desired_bindings     — list of active paper RuntimeBinding dicts the
                                   reconciler must run exactly one worker for
            excluded_binding_ids — set of binding_ids that must be stopped
                                   (paused, retired, failed, or draining)
            excluded_bindings    — list of excluded RuntimeBinding dicts
        None when the runtime-manager is unreachable or returns an error —
        callers must treat None as "unknown desired state" and must not modify
        the running fleet (no starts, no stops).
        """
        if self._store is not None:
            all_bindings = [b.to_dict() for b in self._store.list_all()]
            valid_bindings: List[Dict[str, Any]] = []
            excluded_ids: Set[str] = set()
            excluded: List[Dict[str, Any]] = []
            for b in all_bindings:
                if b.get("deployment_mode") != "paper":
                    continue
                if b.get("status") == "active":
                    is_valid, _ = validate_executable_binding(b)
                    if is_valid:
                        valid_bindings.append(b)
                    else:
                        excluded_ids.add(str(b.get("binding_id")))
                        excluded.append(b)
                else:
                    excluded_ids.add(str(b.get("binding_id")))
                    excluded.append(b)
            return (valid_bindings, excluded_ids, excluded)

        if not self._url:
            return ([], set(), [])
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
            bindings_raw = payload.get("bindings", []) if isinstance(payload, dict) else []
            excluded = payload.get("excluded", []) if isinstance(payload, dict) else []
            excluded_ids: Set[str] = {
                str(e["binding_id"])
                for e in excluded
                if isinstance(e, dict) and e.get("binding_id")
            }
            valid_bindings: List[Dict[str, Any]] = []
            for b in bindings_raw:
                if not isinstance(b, dict):
                    continue
                is_valid, reason = validate_executable_binding(b)
                if is_valid:
                    valid_bindings.append(b)
                else:
                    b_id = str(b.get("binding_id") or "<unknown>")
                    log.warning(
                        "rejecting non-executable paper fleet child binding %s: %s",
                        b_id,
                        reason,
                    )
                    if b.get("binding_id"):
                        excluded_ids.add(str(b["binding_id"]))
                        excluded.append(b)
            return (valid_bindings, excluded_ids, excluded)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._last_error = (
                    f"fleet desired state fetch failed: {type(exc).__name__}: {exc}"
                )
            log.warning(
                "could not fetch fleet desired state from runtime-manager: %s", exc
            )
            return None

    def _fetch_source_snapshot(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch latest stored normalized snapshot from Source Ingest.

        Zero recurring pull: only reads from the local Source Ingest read-only
        snapshot endpoint; never pulls from an external provider.
        """
        if not self._source_ingest_url or not symbol:
            return None
        try:
            import urllib.parse
            import urllib.request

            url = (
                f"{self._source_ingest_url}/api/source-ingest/snapshots/latest"
                f"?symbol={urllib.parse.quote(symbol, safe='')}"
            )
            req = urllib.request.Request(
                url,
                headers={"Accept": "application/json"},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            return payload if isinstance(payload, dict) else None
        except Exception as exc:  # noqa: BLE001
            log.warning("could not fetch Source stored snapshot for %s: %s", symbol, exc)
            return None

    def _resolve_market_snapshot(self, binding: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Resolve the latest market snapshot for a binding."""
        raw = binding.get("market_input")
        if not isinstance(raw, dict):
            metadata = binding.get("metadata") if isinstance(binding.get("metadata"), dict) else {}
            raw = metadata.get("market_input")
        if isinstance(raw, dict):
            return raw
        symbol = _clean_text(binding.get("symbol") or (binding.get("metadata") or {}).get("symbol") or "")
        if self._source_ingest_url and symbol:
            return self._fetch_source_snapshot(symbol)
        return None

    def _check_market_admission(
        self,
        binding: Dict[str, Any],
        *,
        snapshot: Optional[Dict[str, Any]] = None,
    ) -> Optional[SnapshotAdmissionDecision]:
        """Evaluate snapshot admission for a paper runtime binding."""
        metadata = binding.get("metadata") if isinstance(binding.get("metadata"), dict) else {}
        policy = binding.get("market_data_policy") or metadata.get("market_data_policy")
        has_market_requirement = bool(
            policy
            or binding.get("market_input")
            or metadata.get("market_input")
            or metadata.get("session_admission")
        )
        if not has_market_requirement:
            return None

        policy_dict = policy if isinstance(policy, dict) else {}
        session_adm = metadata.get("session_admission") if isinstance(metadata.get("session_admission"), dict) else {}
        max_age = int(
            policy_dict.get("max_age_seconds")
            or session_adm.get("max_age_seconds")
            or self._performance_mark_max_age_seconds
            or 86400
        )
        min_closes = int(policy_dict.get("minimum_closes") or 2)
        symbol = _clean_text(binding.get("symbol") or metadata.get("symbol") or "")
        binding_id = _clean_text(binding.get("binding_id") or "")

        if snapshot is None:
            snapshot = self._resolve_market_snapshot(binding)

        return admit_market_snapshot(
            snapshot,
            expected_symbol=symbol or None,
            max_age_seconds=max_age,
            minimum_closes=min_closes,
            now_iso=_iso_now(),
            binding_id=binding_id,
        )

    def _transition_binding(
        self,
        binding_id: str,
        new_status: str,
        *,
        metadata_patch: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Execute a binding status transition against the backing store or HTTP endpoint."""
        if hasattr(self, "_store") and self._store is not None:
            try:
                self._store.transition_status(
                    binding_id,
                    new_status,
                    metadata_patch=metadata_patch,
                )
                return True
            except Exception as exc:  # noqa: BLE001
                log.warning("failed store transition for binding %s -> %s: %s", binding_id, new_status, exc)
                return False

        if not self._url:
            return True

        try:
            import urllib.request

            headers: Dict[str, str] = {
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
            if self._token:
                headers["Authorization"] = f"Bearer {self._token}"
            body_dict: Dict[str, Any] = {"new_status": new_status}
            if metadata_patch:
                body_dict["metadata_patch"] = metadata_patch
            data = json.dumps(body_dict).encode("utf-8")
            req = urllib.request.Request(
                f"{self._url}/api/runtime-bindings/{binding_id}/transition",
                data=data,
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status in (200, 201)
        except Exception as exc:  # noqa: BLE001
            log.warning("failed HTTP transition for binding %s -> %s: %s", binding_id, new_status, exc)
            return False

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
                    "fence_token": entry.fence_token,
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
            "reconciler_id": self._reconciler_id,
            "is_leader": self._is_leader,
            "fence_token": self._fence_token if self._is_leader else None,
            "leader_store_kind": getattr(self._leader_store, "kind", None),
            "leader_lease_expires_at_ms": (
                self._lease_expires_at_ms if self._is_leader else None
            ),
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


def _build_production_leader_store() -> Any:
    lease_path = str(os.getenv("RECONCILER_LEADER_LEASE_PATH") or "").strip()
    if lease_path:
        return FileFencedLeaderStore(lease_path)

    redis_url = str(
        os.getenv("RECONCILER_LEADER_REDIS_URL")
        or os.getenv("SIGNAL_STORE_URL")
        or "redis://signal-store:6379"
    ).strip()
    if not redis_url.startswith(("redis://", "rediss://")):
        raise RuntimeError(
            "Paper fleet reconciler requires a Redis leader URL or an explicit "
            "RECONCILER_LEADER_LEASE_PATH"
        )
    try:
        import redis
    except ImportError as exc:  # pragma: no cover - runtime image contract
        raise RuntimeError("redis package is required for reconciler leader fencing") from exc
    return RedisFencedLeaderStore(
        redis.Redis.from_url(redis_url, decode_responses=True)
    )


def get_reconciler() -> PaperFleetReconciler:
    global _RECONCILER
    if _RECONCILER is None:
        _RECONCILER = PaperFleetReconciler(
            leader_store=_build_production_leader_store(),
        )
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

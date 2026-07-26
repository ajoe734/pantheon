"""
BFF Downstream Health Monitor (LOOP-AUTO-BFF-002)

Continuous probe worker that checks configured downstream service health
endpoints, emits telemetry for each probe result, and opens or updates
incidents for sustained failures.

Design invariants
-----------------
- Non-blocking: runs as an asyncio background task so BFF request handling
  is never blocked by probe latency or downstream unavailability.
- Fail-safe: telemetry emit and incident open errors are logged and swallowed;
  they do not propagate to the probe loop or to BFF request handlers.
- Idempotent: incidents use a stable sentinel id derived from the target name
  so duplicate probes do not create duplicate incidents.
- Degraded mode safe: a degraded or unreachable downstream does not affect
  active runtime workers.  The probe result is surfaced in the BFF operator
  health status, not injected into the trading execution path.

Telemetry sentinel values
-------------------------
BFF health probes are not tied to a specific RuntimeBinding.  The telemetry
ingest contract (TEL-001) requires trading-context fields (binding_id, plan_id,
etc.).  Health probe events use stable sentinel values in those positions:

  binding_id              : "bff-health-probe"
  execution_mode          : "paper"  (closest valid sentinel)
  deployment_stage        : "paper"
  runtime_id              : "bff-health-monitor"
  capital_pool_id         : "bff-control-plane"
  artifact_id             : "bff-operator"
  artifact_version        : "0"
  plan_id                 : "bff-health-probe"
  persona_capital_binding_id : "bff-health-probe"

When a binding_store is configured in the telemetry service, these sentinel
values will not resolve to a real binding and the event will be DLQ'd.  This
is expected behaviour in production; the operator-visible health state is the
primary truth surface for BFF downstream health.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sentinel probe identity (non-trading)
# ---------------------------------------------------------------------------

_SENTINEL_BINDING_ID = "00000000-0000-4000-8000-0000000000b1"
_SENTINEL_RUNTIME_ID = "bff-health-monitor"
_SENTINEL_CAPITAL_POOL_ID = "bff-control-plane"
_SENTINEL_ARTIFACT_ID = "bff-operator"
_SENTINEL_ARTIFACT_VERSION = "0.1.0"
_SENTINEL_PLAN_ID = "bff-health-probe"
_SENTINEL_PCB_ID = "bff-health-probe"
_SENTINEL_STAGE = "paper"

# ---------------------------------------------------------------------------
# Probe result
# ---------------------------------------------------------------------------


class DownstreamProbeResult:
    """Outcome of a single health probe against one downstream service."""

    __slots__ = (
        "target_name",
        "ok",
        "status_code",
        "latency_ms",
        "checked_at",
        "failure_reason",
        "consecutive_failures",
    )

    def __init__(
        self,
        *,
        target_name: str,
        ok: bool,
        status_code: int,
        latency_ms: float,
        checked_at: str,
        failure_reason: Optional[str] = None,
        consecutive_failures: int = 0,
    ) -> None:
        self.target_name = target_name
        self.ok = ok
        self.status_code = status_code
        self.latency_ms = latency_ms
        self.checked_at = checked_at
        self.failure_reason = failure_reason
        self.consecutive_failures = consecutive_failures

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_name": self.target_name,
            "ok": self.ok,
            "status_code": self.status_code,
            "latency_ms": round(self.latency_ms, 1),
            "checked_at": self.checked_at,
            "failure_reason": self.failure_reason,
            "consecutive_failures": self.consecutive_failures,
        }


# ---------------------------------------------------------------------------
# Default probe targets
# ---------------------------------------------------------------------------

_DEFAULT_TARGET_ENV_VARS: List[Dict[str, str]] = [
    {"name": "telemetry", "url_env": "PANTHEON_TELEMETRY_API_URL"},
    {"name": "incidents", "url_env": "PANTHEON_INCIDENTS_API_URL"},
    {"name": "runtime-manager", "url_env": "PANTHEON_RUNTIME_MANAGER_URL"},
    {"name": "persona", "url_env": "PANTHEON_PERSONA_API_URL"},
    {"name": "deployment", "url_env": "PANTHEON_DEPLOYMENT_API_URL"},
]

# ---------------------------------------------------------------------------
# Internal HTTP helpers (synchronous — wrapped with asyncio.to_thread)
# ---------------------------------------------------------------------------


def _utc_now_rfc3339() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _probe_http(url: str, timeout: float) -> tuple[bool, int, str]:
    """Return (ok, status_code, failure_reason)."""
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, resp.status, ""
    except urllib.error.HTTPError as exc:
        return False, exc.code, f"HTTP {exc.code}"
    except urllib.error.URLError as exc:
        reason = str(getattr(exc, "reason", exc))
        return False, -1, f"URLError: {reason}"
    except OSError as exc:
        return False, -1, f"OSError: {exc}"
    except Exception as exc:  # noqa: BLE001
        return False, -1, f"unexpected: {exc}"


def _post_json(url: str, body: Dict[str, Any], timeout: float) -> tuple[bool, int]:
    """POST a JSON body.  Returns (ok, status_code)."""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, resp.status
    except urllib.error.HTTPError as exc:
        return False, exc.code
    except (urllib.error.URLError, OSError, Exception):  # noqa: BLE001
        return False, -1


# ---------------------------------------------------------------------------
# DownstreamHealthMonitor
# ---------------------------------------------------------------------------


class DownstreamHealthMonitor:
    """
    Async background worker that probes BFF downstream service health.

    Usage::

        monitor = DownstreamHealthMonitor()
        await monitor.start()           # launches background probe loop
        state = monitor.get_state()     # read current probe snapshot
        await monitor.stop()            # graceful shutdown

    The monitor is started once at BFF app startup and stopped on shutdown.
    ``get_state()`` is safe to call from any coroutine without locking because
    the probe loop writes atomically to the ``_state`` dict.
    """

    def __init__(
        self,
        *,
        telemetry_url: Optional[str] = None,
        incidents_url: Optional[str] = None,
        probe_interval_seconds: Optional[float] = None,
        failure_threshold: Optional[int] = None,
        health_path: str = "/__health__",
        http_timeout: float = 3.0,
    ) -> None:
        self._telemetry_url = (
            telemetry_url
            or os.getenv("PANTHEON_TELEMETRY_API_URL", "").strip().rstrip("/")
            or os.getenv("PANTHEON_TELEMETRY_URL", "").strip().rstrip("/")
        )
        self._incidents_url = (
            incidents_url
            or os.getenv("PANTHEON_INCIDENTS_API_URL", "").strip().rstrip("/")
            or os.getenv("PANTHEON_INCIDENTS_URL", "").strip().rstrip("/")
        )
        self._probe_interval = float(
            probe_interval_seconds
            if probe_interval_seconds is not None
            else os.getenv("PANTHEON_BFF_HEALTH_PROBE_INTERVAL_SECONDS", "30")
        )
        self._failure_threshold = int(
            failure_threshold
            if failure_threshold is not None
            else os.getenv("PANTHEON_BFF_HEALTH_FAILURE_THRESHOLD", "3")
        )
        self._health_path = health_path
        self._http_timeout = http_timeout

        # Per-target mutable state (written only from probe loop)
        self._state: Dict[str, DownstreamProbeResult] = {}
        self._failure_counts: Dict[str, int] = {}
        self._open_incident_ids: Dict[str, str] = {}  # target_name -> incident_id

        self._task: Optional[asyncio.Task[None]] = None
        self._running = False

    # ------------------------------------------------------------------
    # Public lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the background probe loop (idempotent)."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._probe_loop(), name="downstream-health-monitor")
        log.info(
            "DownstreamHealthMonitor started (interval=%.0fs, failure_threshold=%d)",
            self._probe_interval,
            self._failure_threshold,
        )

    async def stop(self) -> None:
        """Stop the background probe loop gracefully."""
        self._running = False
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        log.info("DownstreamHealthMonitor stopped")

    # ------------------------------------------------------------------
    # State read — safe from any coroutine
    # ------------------------------------------------------------------

    def get_state(self) -> Dict[str, Any]:
        """Return a snapshot of the most-recent probe result per target."""
        return {
            "targets": {name: result.to_dict() for name, result in self._state.items()},
            "overall_ok": all(r.ok for r in self._state.values()) if self._state else None,
            "probe_interval_seconds": self._probe_interval,
            "failure_threshold": self._failure_threshold,
        }

    def get_degraded_targets(self) -> List[str]:
        """Return names of currently degraded (non-ok) downstream targets."""
        return [name for name, result in self._state.items() if not result.ok]

    # ------------------------------------------------------------------
    # Probe loop
    # ------------------------------------------------------------------

    async def _probe_loop(self) -> None:
        while self._running:
            try:
                await self._probe_all()
            except Exception as exc:  # noqa: BLE001
                log.warning("probe_loop: unexpected error: %s", exc)
            try:
                await asyncio.sleep(self._probe_interval)
            except asyncio.CancelledError:
                break

    async def _probe_all(self) -> None:
        targets = self._resolve_targets()
        if not targets:
            return
        tasks = [self._probe_one(name, base_url) for name, base_url in targets.items()]
        results: List[DownstreamProbeResult] = await asyncio.gather(*tasks, return_exceptions=False)
        for result in results:
            self._state[result.target_name] = result
            await self._handle_probe_result(result)

    def _resolve_targets(self) -> Dict[str, str]:
        """Build a {name: base_url} mapping from environment variables."""
        targets: Dict[str, str] = {}
        for spec in _DEFAULT_TARGET_ENV_VARS:
            base_url = os.getenv(spec["url_env"], "").strip().rstrip("/")
            if base_url:
                targets[spec["name"]] = base_url
        return targets

    async def _probe_one(self, name: str, base_url: str) -> DownstreamProbeResult:
        url = f"{base_url}{self._health_path}"
        t0 = time.monotonic()
        ok, status_code, failure_reason = await asyncio.to_thread(
            _probe_http, url, self._http_timeout
        )
        latency_ms = (time.monotonic() - t0) * 1000.0
        prev = self._state.get(name)
        if ok:
            consecutive_failures = 0
        else:
            prev_failures = prev.consecutive_failures if prev and not prev.ok else 0
            consecutive_failures = prev_failures + 1

        return DownstreamProbeResult(
            target_name=name,
            ok=ok,
            status_code=status_code,
            latency_ms=latency_ms,
            checked_at=_utc_now_rfc3339(),
            failure_reason=failure_reason if not ok else None,
            consecutive_failures=consecutive_failures,
        )

    # ------------------------------------------------------------------
    # Telemetry + incident side-effects (all failures tolerated)
    # ------------------------------------------------------------------

    async def _handle_probe_result(self, result: DownstreamProbeResult) -> None:
        # Always emit telemetry for each probe
        await asyncio.to_thread(self._emit_telemetry_sync, result)

        if not result.ok and result.consecutive_failures >= self._failure_threshold:
            await asyncio.to_thread(self._open_or_update_incident_sync, result)
        elif result.ok and result.target_name in self._open_incident_ids:
            # Service recovered — clear local incident tracking (incident stays open
            # in the incidents service; resolution requires operator action per policy)
            incident_id = self._open_incident_ids.pop(result.target_name)
            log.info(
                "DownstreamHealthMonitor: %s recovered after sustained failure; "
                "incident %s remains open for operator review",
                result.target_name,
                incident_id,
            )

    def _emit_telemetry_sync(self, result: DownstreamProbeResult) -> None:
        """POST a runtime_health telemetry event to the telemetry ingest API."""
        if not self._telemetry_url:
            return
        event = {
            "event_id": str(uuid.uuid4()),
            "event_type": "runtime_health",
            "created_at": result.checked_at,
            "execution_mode": _SENTINEL_STAGE,
            "deployment_stage": _SENTINEL_STAGE,
            "binding_id": _SENTINEL_BINDING_ID,
            "runtime_id": _SENTINEL_RUNTIME_ID,
            "capital_pool_id": _SENTINEL_CAPITAL_POOL_ID,
            "artifact_id": _SENTINEL_ARTIFACT_ID,
            "artifact_version": _SENTINEL_ARTIFACT_VERSION,
            "plan_id": _SENTINEL_PLAN_ID,
            "persona_capital_binding_id": _SENTINEL_PCB_ID,
            "target": {
                "strategy_id": f"bff-health-{result.target_name}",
                "registry_id": _SENTINEL_BINDING_ID,
                "artifact_version": _SENTINEL_ARTIFACT_VERSION,
            },
            "metrics": {
                "probe_ok": 1 if result.ok else 0,
                "status_code": result.status_code,
                "latency_ms": round(result.latency_ms, 1),
                "consecutive_failures": result.consecutive_failures,
            },
            "trace_id": str(uuid.uuid4()),
            # Non-standard extension fields for BFF health context
            "bff_health_probe": {
                "target_name": result.target_name,
                "failure_reason": result.failure_reason,
                "probe_source": "bff-downstream-health-monitor",
            },
        }
        ok, status = _post_json(
            f"{self._telemetry_url}/api/telemetry/ingest",
            event,
            self._http_timeout,
        )
        if not ok:
            log.debug(
                "DownstreamHealthMonitor: telemetry emit for %s returned status %d",
                result.target_name,
                status,
            )

    def _open_or_update_incident_sync(self, result: DownstreamProbeResult) -> Optional[str]:
        """Open or locate an incident for a sustained downstream failure."""
        if not self._incidents_url:
            return None

        target_name = result.target_name
        # Use a stable sentinel incident id so duplicate probes are idempotent
        sentinel_incident_id = f"bff-downstream-{target_name}-degraded"

        if target_name in self._open_incident_ids:
            # Already tracking an open incident — no duplicate creation
            return self._open_incident_ids[target_name]

        body = {
            "incident_id": sentinel_incident_id,
            "title": f"BFF downstream degradation: {target_name} unreachable",
            "status": "open",
            "severity": "high",
            # Sentinel trading-context fields (required by incident schema)
            "binding_id": _SENTINEL_BINDING_ID,
            "deployment_stage": _SENTINEL_STAGE,
            "deployment_plan_id": _SENTINEL_PLAN_ID,
            "capital_pool_id": _SENTINEL_CAPITAL_POOL_ID,
            "persona_capital_binding_id": _SENTINEL_PCB_ID,
            "artifact_id": _SENTINEL_ARTIFACT_ID,
            "artifact_version": _SENTINEL_ARTIFACT_VERSION,
            "runtime_id": _SENTINEL_RUNTIME_ID,
            "trace_id": str(uuid.uuid4()),
            "evidence_summary": (
                f"BFF downstream health monitor detected {result.consecutive_failures} "
                f"consecutive probe failures for {target_name}. "
                f"Last failure: {result.failure_reason or 'unknown'}. "
                f"Last checked at: {result.checked_at}."
            ),
            "telemetry_event_ids": [],
        }
        ok, status = _post_json(
            f"{self._incidents_url}/api/incidents",
            body,
            self._http_timeout,
        )
        if ok or status == 409:
            # 201 = created, 409 = already exists (idempotent)
            self._open_incident_ids[target_name] = sentinel_incident_id
            log.warning(
                "DownstreamHealthMonitor: incident %s for %s (status=%d, failures=%d)",
                sentinel_incident_id,
                target_name,
                status,
                result.consecutive_failures,
            )
            return sentinel_incident_id
        else:
            log.debug(
                "DownstreamHealthMonitor: incident creation for %s returned status %d",
                target_name,
                status,
            )
            return None

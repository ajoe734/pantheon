"""Owner-attributed runtime retirement telemetry for runtime-manager.

RuntimeBindingStore calls the registered observer after a binding becomes
``retired`` and the new state is persisted.  The emitter turns that into one
``runtime_retired`` TelemetryEvent so the telemetry runtime summary can leave
retired runtimes out of its default listing.

The event id is a UUIDv5 of ``binding_id`` + ``retired_at``; replays (retries,
recovery re-runs) therefore collapse on telemetry's event-id idempotency.
Delivery failure never undoes the retirement: the binding store stays the
source of truth and downstream runtime-manager lookups remain as defence.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
import time
import uuid
from datetime import datetime, timezone
import contextlib
import threading
from typing import Any, Callable, ContextManager, Dict, List, Optional

from services.runtime_manager.runtime_binding import RuntimeBinding

logger = logging.getLogger(__name__)

RUNTIME_RETIRED_EVENT_TYPE = "runtime_retired"
_EVENT_ID_NAMESPACE = uuid.UUID("5d0c7f0e-6b1e-4f43-9d52-7a1c2a3e9b10")
_WRITE_OWNER = "runtime_manager"

Sender = Callable[[Dict[str, Any]], bool]


def retirement_event_id(binding_id: str, retired_at: str) -> str:
    return str(uuid.uuid5(_EVENT_ID_NAMESPACE, f"{binding_id}|{retired_at}"))


def build_retirement_event(binding: RuntimeBinding, tenant_id: str = "") -> Dict[str, Any]:
    """Build the schema-valid retirement event for a retired binding."""
    if not binding.retired_at:
        raise ValueError("retired binding must carry retired_at")
    event = {
        "event_id": retirement_event_id(binding.binding_id, binding.retired_at),
        "event_type": RUNTIME_RETIRED_EVENT_TYPE,
        # created_at == retired_at: ingest rejects events after retired_at.
        "created_at": binding.retired_at,
        "execution_mode": binding.execution_mode,
        "deployment_stage": binding.deployment_mode,
        "environment": binding.deployment_mode,
        "binding_id": binding.binding_id,
        "runtime_id": binding.runtime_id,
        "capital_pool_id": binding.capital_pool_id,
        "artifact_id": binding.artifact_id,
        "artifact_version": binding.artifact_version,
        "plan_id": binding.plan_id,
        "persona_capital_binding_id": binding.persona_capital_binding_id,
        "target": {
            "strategy_id": binding.artifact_id,
            "artifact_version": binding.artifact_version,
            "promotion_state": "retired",
        },
        "metrics": {"action": RUNTIME_RETIRED_EVENT_TYPE},
        "metadata": {
            "retired_at": binding.retired_at,
            "runtime_binding_effective_at": binding.effective_at,
            "emitted_by": _WRITE_OWNER,
        },
        "authority_refs": {
            "write_owner": _WRITE_OWNER,
            "authority_source": "runtime_binding",
        },
    }
    # Prefer the binding's own tenant; fall back to the sender's tenant.
    # Telemetry binds the event to the authenticated request tenant and
    # rejects a payload tenant that disagrees with it.
    binding_tenant = str((binding.metadata or {}).get("tenant_id") or "").strip()
    event_tenant = binding_tenant or tenant_id
    if event_tenant:
        event["tenant_id"] = event_tenant
    return event


class HttpTelemetrySender:
    """POST one event to the telemetry ingest route (service-token auth)."""

    def __init__(self, url: str, token: str = "", tenant_id: str = "default", timeout: float = 5.0) -> None:
        self._url = url.rstrip("/")
        self._token = token
        self._tenant_id = tenant_id
        self._timeout = timeout

    @classmethod
    def from_env(cls) -> Optional["HttpTelemetrySender"]:
        url = (
            os.getenv("PANTHEON_TELEMETRY_API_URL")
            or os.getenv("PANTHEON_TELEMETRY_URL")
            or ""
        ).strip()
        if not url:
            return None
        tenant_id = (
            os.getenv("PANTHEON_TELEMETRY_TENANT_ID")
            or os.getenv("PANTHEON_DEPLOYMENT_TENANT_ID")
            or "default"
        ).strip()
        return cls(
            url,
            token=os.getenv("PANTHEON_TELEMETRY_SERVICE_TOKEN", ""),
            tenant_id=tenant_id,
        )

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    def __call__(self, event: Dict[str, Any]) -> bool:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Tenant-Id": self._tenant_id,
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        request = urllib.request.Request(
            f"{self._url}/api/telemetry/ingest",
            data=json.dumps(event, allow_nan=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return getattr(response, "status", 200) in {200, 202}
        except (urllib.error.URLError, OSError, ValueError):
            return False


ACK_EVENT_ID_KEY = "retirement_telemetry_event_id"
ACK_AT_KEY = "retirement_telemetry_acked_at"
SWEEP_BATCH_LIMIT = 25


class RetirementTelemetryEmitter:
    """Store observer: emit one retirement event per retired binding.

    A telemetry acknowledgement is recorded on the binding metadata
    (``retirement_telemetry_event_id`` / ``retirement_telemetry_acked_at``).
    ``sweep`` re-emits retired bindings that lack an acknowledgement for their
    current event id; the idempotent event id makes replays safe.
    """

    def __init__(
        self,
        sender: Optional[Sender],
        store: Any = None,
        lock: Optional[ContextManager[Any]] = None,
        tenant_id: Optional[str] = None,
    ) -> None:
        self._sender = sender
        self._store = store
        self._lock = lock if lock is not None else contextlib.nullcontext()
        self._tenant_id = (
            tenant_id
            if tenant_id is not None
            else str(getattr(sender, "tenant_id", "") or "")
        )

    def __call__(self, binding: RuntimeBinding) -> None:
        self.emit(binding)

    def emit(self, binding: RuntimeBinding) -> bool:
        if self._sender is None:
            return False
        try:
            event = build_retirement_event(binding, self._tenant_id)
            if not self._sender(event):
                logger.warning(
                    "runtime retirement telemetry not accepted binding_id=%s",
                    binding.binding_id,
                )
                return False
            self._record_ack(binding, event["event_id"])
            return True
        except Exception:  # noqa: BLE001 - never undo a committed retirement
            logger.exception(
                "runtime retirement telemetry failed binding_id=%s", binding.binding_id
            )
            return False

    def _record_ack(self, binding: RuntimeBinding, event_id: str) -> None:
        if self._store is None:
            return
        with self._lock:
            self._store.patch_metadata(
                binding.binding_id,
                {ACK_EVENT_ID_KEY: event_id, ACK_AT_KEY: _utc_now()},
            )

    def pending(self) -> List[RuntimeBinding]:
        """Retired bindings whose current retirement event is unacknowledged."""
        if self._store is None:
            return []
        out: List[RuntimeBinding] = []
        for binding in self._store.list_all():
            if binding.status != "retired" or not binding.retired_at:
                continue
            expected = retirement_event_id(binding.binding_id, binding.retired_at)
            meta = binding.metadata or {}
            if meta.get(ACK_EVENT_ID_KEY) == expected and meta.get(ACK_AT_KEY):
                continue
            out.append(binding)
        return out

    def sweep(self, limit: int = SWEEP_BATCH_LIMIT) -> int:
        """Re-emit up to ``limit`` unacknowledged retirements; return acked count."""
        if self._sender is None:
            return 0
        acked = 0
        for binding in self.pending()[: max(0, limit)]:
            if self.emit(binding):
                acked += 1
        return acked

    def start_background_sweep(self, interval_seconds: float = 300.0) -> Optional[threading.Thread]:
        """Sweep once at startup, then periodically, on a daemon thread."""
        if self._sender is None or self._store is None:
            return None

        def _run() -> None:
            while True:
                try:
                    self.sweep()
                except Exception:  # noqa: BLE001
                    logger.exception("runtime retirement telemetry sweep failed")
                time.sleep(interval_seconds)

        thread = threading.Thread(
            target=_run, name="retirement-telemetry-sweep", daemon=True
        )
        thread.start()
        return thread


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

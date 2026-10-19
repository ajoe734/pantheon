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
import uuid
from typing import Any, Callable, Dict, Optional

from services.runtime_manager.runtime_binding import RuntimeBinding

logger = logging.getLogger(__name__)

RUNTIME_RETIRED_EVENT_TYPE = "runtime_retired"
_EVENT_ID_NAMESPACE = uuid.UUID("5d0c7f0e-6b1e-4f43-9d52-7a1c2a3e9b10")
_WRITE_OWNER = "runtime_manager"

Sender = Callable[[Dict[str, Any]], bool]


def retirement_event_id(binding_id: str, retired_at: str) -> str:
    return str(uuid.uuid5(_EVENT_ID_NAMESPACE, f"{binding_id}|{retired_at}"))


def build_retirement_event(binding: RuntimeBinding) -> Dict[str, Any]:
    """Build the schema-valid retirement event for a retired binding."""
    if not binding.retired_at:
        raise ValueError("retired binding must carry retired_at")
    return {
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
        "target": {"runtime_id": binding.runtime_id},
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
        return cls(
            url,
            token=os.getenv("PANTHEON_TELEMETRY_SERVICE_TOKEN", ""),
            tenant_id=os.getenv("PANTHEON_TELEMETRY_TENANT_ID", "default"),
        )

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


class RetirementTelemetryEmitter:
    """Store observer: emit one retirement event per retired binding."""

    def __init__(self, sender: Optional[Sender]) -> None:
        self._sender = sender

    def __call__(self, binding: RuntimeBinding) -> None:
        if self._sender is None:
            return
        try:
            event = build_retirement_event(binding)
            if not self._sender(event):
                logger.warning(
                    "runtime retirement telemetry not accepted binding_id=%s",
                    binding.binding_id,
                )
        except Exception:  # noqa: BLE001 - never undo a committed retirement
            logger.exception(
                "runtime retirement telemetry failed binding_id=%s", binding.binding_id
            )

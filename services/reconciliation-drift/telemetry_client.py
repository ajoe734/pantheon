"""Authenticated read of the telemetry-owned runtime-summary projection."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


class TelemetryError(RuntimeError):
    """Telemetry runtime summaries could not be read."""


class TelemetryAuthError(TelemetryError):
    """Telemetry rejected the service credential or tenant (HTTP 401/403)."""


class TelemetryUnavailable(TelemetryError):
    """Telemetry was unreachable or returned an unusable response."""


def fetch_runtime_summaries(
    telemetry_url: str,
    *,
    tenant_id: str | None = None,
    service_token: str | None = None,
    timeout_seconds: float = 10.0,
) -> list[dict[str, Any]]:
    if not telemetry_url:
        raise TelemetryUnavailable("PANTHEON_TELEMETRY_API_URL is required")
    if service_token is None:
        service_token = os.getenv("PANTHEON_TELEMETRY_SERVICE_TOKEN", "")
    token = service_token.strip()
    tenant = (tenant_id or os.getenv("PANTHEON_TENANT_ID") or "default").strip()
    headers = {"Accept": "application/json", "X-Tenant-Id": tenant}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        telemetry_url.rstrip("/") + "/api/telemetry/runtime-summaries",
        headers=headers,
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_type = TelemetryAuthError if exc.code in (401, 403) else TelemetryUnavailable
        raise error_type(f"telemetry runtime summaries rejected: HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise TelemetryUnavailable(f"telemetry service unavailable: {exc}") from exc

    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError as exc:
        raise TelemetryUnavailable("telemetry runtime summaries returned invalid JSON") from exc
    if isinstance(payload, dict):
        payload = payload.get("summaries") or payload.get("items") or []
    if not isinstance(payload, list):
        raise TelemetryUnavailable("telemetry runtime summaries must be a list")
    return [item for item in payload if isinstance(item, dict)]
